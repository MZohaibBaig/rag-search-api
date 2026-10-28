import threading
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from sqlalchemy.exc import DataError

from app.database import get_db
from app.demo import DEMO_USERNAME
from app.extract import IngestError, clean_filename, extract_text
from app.limits import MAX_UPLOAD_BYTES, per_ip_limit
from app.models import User, Document
from app.schemas import DocumentResponse, DocumentWithChunks
from app.auth import get_current_user
from app.rag import ingest_text
from app.groq_client import get_groq_answer

router = APIRouter(prefix="/documents", tags=["documents"])

MAX_DOCS_PER_USER = 10
MAX_UPLOADS_PER_DAY = 30      # across all users, rolling 24 hours

# One ingest at a time keeps peak memory at a single document. Non-blocking: a second upload is
# told to retry rather than queued behind the first.
_ingest_lock = threading.Lock()


def _check_quotas(db: Session, user: User) -> None:
    """DB-backed, so the caps still hold after the app sleeps and restarts."""
    owned = db.query(func.count(Document.id)).filter(Document.user_id == user.id).scalar()
    if owned >= MAX_DOCS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Document limit reached ({MAX_DOCS_PER_USER}). Delete one to upload another."
        )
    today = db.query(func.count(Document.id)).join(User, Document.user_id == User.id).filter(
        User.username != DEMO_USERNAME,
        Document.uploaded_at > datetime.utcnow() - timedelta(hours=24),
    ).scalar()
    if today >= MAX_UPLOADS_PER_DAY:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="The demo has reached its daily upload limit. Please try again tomorrow."
        )


@router.post("/upload", response_model=DocumentWithChunks,
             dependencies=[Depends(per_ip_limit("upload", 10))])
def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Upload a .txt, .pdf, .docx, .xlsx or .csv file (max 5 MB), chunk it, embed it, and store in database.

    A plain def, not async: parsing and embedding are CPU-bound and run in the threadpool,
    leaving the event loop free.
    """
    if not _ingest_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Another upload is in progress, try again shortly"
        )
    try:
        _check_quotas(db, current_user)
        content = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise IngestError(413, f"File too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
        filename = clean_filename(file.filename)
        text = extract_text(filename, content)
        del content
        try:
            return ingest_text(db, current_user.id, filename, text)
        except DataError as exc:
            raise IngestError(422, "Could not read this file") from exc
    except Exception:
        db.rollback()
        raise
    finally:
        _ingest_lock.release()


@router.get("/", response_model=list[DocumentResponse])
def list_documents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all documents belonging to the current user."""
    documents = db.query(Document).filter(
        Document.user_id == current_user.id
    ).order_by(desc(Document.uploaded_at)).all()
    return documents

@router.get("/{document_id}", response_model=DocumentWithChunks)
def get_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a specific document with its chunks (user-scoped)."""
    document = db.query(Document).filter(
        Document.id == document_id,
        Document.user_id == current_user.id
    ).first()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    return document

@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a document (user-scoped). Cascades to chunks and query logs."""
    document = db.query(Document).filter(
        Document.id == document_id,
        Document.user_id == current_user.id
    ).first()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    db.delete(document)
    db.commit()