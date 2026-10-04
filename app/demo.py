import logging
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.database import SessionLocal, get_db, init_db
from app.groq_client import get_groq_answer
from app.models import Document, DocumentChunk, User
from app.rag import INGEST_VERSION, NO_ANSWER, build_context, ingest_text, retrieve

logger = logging.getLogger("uvicorn.error")

DEMO_USERNAME = "demo"
DEMO_FILENAME = "brindlemoor-lighthouse.txt"
DEMO_DOC_PATH = Path(__file__).parent / "demo_data" / DEMO_FILENAME
DEMO_PAGE_PATH = Path(__file__).resolve().parent.parent / "static" / "demo.html"

RATE_LIMIT = 10          # requests
RATE_WINDOW_SECONDS = 60

EXAMPLES = [
    {"question": "Who designed Brindlemoor Lighthouse, and why was it built?", "unanswerable": False},
    {"question": "How does the light's flash pattern identify it to sailors?", "unanswerable": False},
    {"question": "What is the best recipe for sourdough bread?", "unanswerable": True},
]

router = APIRouter(prefix="/demo", tags=["demo"])


def seed_demo_document() -> None:
    """On first boot, ingest the demo document through the normal upload pipeline (rag.ingest_text)."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == DEMO_USERNAME).first()
        if not user:
            # Random password nobody knows: this account exists only to own the demo document.
            user = User(
                username=DEMO_USERNAME,
                email="demo@localhost.invalid",
                hashed_password=hash_password(secrets.token_urlsafe(32)),
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        existing = db.query(Document).filter(
            Document.user_id == user.id, Document.filename == DEMO_FILENAME
        ).order_by(Document.id.desc()).all()
        current = next((
            d for d in existing
            if d.ingest_version == INGEST_VERSION
            and db.query(DocumentChunk).filter(DocumentChunk.document_id == d.id).first()
        ), None)

        if current is None:
            # Missing, built by older chunking/embeddings, or left without chunks. Build the new
            # copy first: requests keep being served from the old one until it's committed.
            text = DEMO_DOC_PATH.read_text(encoding="utf-8")
            current = ingest_text(db, user.id, DEMO_FILENAME, text)
            logger.info("Seeded demo document %s (ingest version %s)", DEMO_FILENAME, INGEST_VERSION)

        stale = [d for d in existing if d.id != current.id]
        for document in stale:
            db.delete(document)
        if stale:
            db.commit()
            logger.info("Removed %d stale copy(ies) of the demo document", len(stale))
    except Exception:
        db.rollback()
        logger.exception("Demo document seeding failed; /demo/ask will return 503 until it succeeds")
    finally:
        db.close()


# In-memory sliding window per client IP. Per-process; resets on restart. Fine for a demo.
_hits: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # Behind a proxy (Railway) the rightmost X-Forwarded-For entry is the one the proxy added.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request) -> None:
    now = time.monotonic()
    hits = _hits[_client_ip(request)]
    while hits and now - hits[0] > RATE_WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= RATE_LIMIT:
        retry_after = max(1, int(RATE_WINDOW_SECONDS - (now - hits[0])) + 1)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit reached ({RATE_LIMIT} questions per minute). Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )
    hits.append(now)


class DemoQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@router.get("", include_in_schema=False)
def demo_page():
    return FileResponse(DEMO_PAGE_PATH, media_type="text/html")


@router.get("/examples")
def demo_examples():
    return {"filename": DEMO_FILENAME, "examples": EXAMPLES}


def find_demo_document(db: Session) -> Document | None:
    # Newest first: while a reseed swaps in a rebuilt copy, the new one wins
    return db.query(Document).join(User, Document.user_id == User.id).filter(
        User.username == DEMO_USERNAME, Document.filename == DEMO_FILENAME
    ).order_by(Document.id.desc()).first()


# Serializes self-healing so concurrent requests don't seed the demo document twice.
_heal_lock = threading.Lock()


def get_demo_document(db: Session) -> Document | None:
    """Find the demo document. If the tables or the document are missing (e.g. the database
    was wiped under a running app), rebuild them once and retry. None if still unavailable."""
    def lookup() -> Document | None:
        try:
            return find_demo_document(db)
        except SQLAlchemyError:
            db.rollback()
            return None

    document = lookup()
    if document:
        return document

    with _heal_lock:
        # Another request may have healed while we waited for the lock
        document = lookup()
        if document:
            return document
        try:
            logger.warning("Demo document missing; re-creating tables and re-seeding")
            init_db()
            seed_demo_document()
            return find_demo_document(db)
        except SQLAlchemyError:
            db.rollback()
            logger.exception("Demo self-heal failed")
            return None


@router.post("/ask", dependencies=[Depends(rate_limit)])
def demo_ask(payload: DemoQuestion, db: Session = Depends(get_db)):
    """Read-only, no-auth Q&A scoped to the seeded demo document. Writes nothing
    per request (it only re-seeds when the demo document is missing)."""
    document = get_demo_document(db)
    if not document:
        raise HTTPException(status_code=503, detail="Demo document is not available yet.")

    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Question is empty.")

    t0 = perf_counter()
    retrieval = retrieve(db, document.id, question)
    retrieve_ms = round((perf_counter() - t0) * 1000)

    chunks = [
        {
            "chunk_index": hit.chunk.chunk_index,
            "chunk_text": hit.chunk.chunk_text,
            "distance": round(hit.distance, 4),
            "similarity": round(1 - hit.distance, 4),
            "matched_by": hit.matched_by,
        }
        for hit in retrieval.hits
    ]

    t1 = perf_counter()
    if retrieval.abstain:
        # Nothing in the document is on topic: don't spend an LLM call on it
        answer, status, error = NO_ANSWER, "ok", None
    else:
        try:
            answer = get_groq_answer(question, build_context([hit.chunk for hit in retrieval.hits]))
            status, error = "ok", None
        except Exception:
            logger.exception("Groq call failed for /demo/ask")
            answer, status = None, "generation_failed"
            error = "The answer service is unavailable right now. The retrieved chunks above are still real."
    generate_ms = round((perf_counter() - t1) * 1000)

    return {
        "status": status,
        "abstained": retrieval.abstain,
        "answer": answer,
        "error": error,
        "chunks": chunks,
        "retrieve_ms": retrieve_ms,
        "generate_ms": generate_ms,
    }
