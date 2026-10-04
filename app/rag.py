import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk
from app.chunking import chunk_text
from app.embeddings import embed_text, embed_batch

# Bump whenever chunking or embeddings change in a way that makes stored chunks stale.
# seed_demo_document() rebuilds the demo document when its stored version differs.
#   1 = fixed 500-char windows (before versioning existed these rows hold 0)
#   2 = section/sentence-aware chunks prefixed with their heading
INGEST_VERSION = 2

TOP_K = 5
CANDIDATES = 20          # taken from each of the semantic and keyword searches before fusion
RRF_K = 60               # Reciprocal Rank Fusion constant (the usual default)
# Best cosine similarity at or above which a full question counts as "well covered";
# below it, multi-clause questions are also searched clause by clause.
COMPOUND_TRIGGER = 0.5
FULL_QUESTION_WEIGHT = 2.0
# Below this best cosine similarity nothing in the document is on topic: abstain, no LLM call.
# Calibrated on tests/eval (python -m tests.eval.run_eval --questions).
ABSTAIN_SIMILARITY = 0.15

NO_ANSWER = "I can't answer that from this document."

_CLAUSE_SPLIT =re.compile(r"\s*(?:[,;?]|\band\b|\bas well as\b|\bplus\b)\s*", re.IGNORECASE)


@dataclass
class Hit:
    chunk: DocumentChunk
    distance: float      # cosine distance to the full question (lower = more similar)
    matched_by: str      # "semantic", "keyword" or "both": which candidate lists held the chunk


@dataclass
class Retrieval:
    hits: list[Hit]
    abstain: bool        # True when the document has nothing on topic for this question


def ingest_text(db: Session, user_id: int, filename: str, text: str) -> Document:
    """Create a document, chunk + embed its text, and store the chunks, in one transaction."""
    chunks = chunk_text(text)
    embeddings = embed_batch(chunks)

    document = Document(user_id=user_id, filename=filename, ingest_version=INGEST_VERSION)
    db.add(document)
    db.flush()  # assigns document.id; nothing is visible to others until the commit below

    for chunk_index, (chunk_text_str, embedding) in enumerate(zip(chunks, embeddings)):
        db.add(DocumentChunk(
            document_id=document.id,
            chunk_text=chunk_text_str,
            embedding=embedding,
            chunk_index=chunk_index
        ))

    db.commit()
    db.refresh(document)
    return document


def split_clauses(question: str) -> list[str]:
    """Clauses of a compound question ("who designed it, and why was it built?")."""
    parts = (p.strip() for p in _CLAUSE_SPLIT.split(question))
    return [p for p in parts if len(p.split()) >= 2]


def _semantic_search(db: Session, document_id: int, embedding: list[float], n: int) -> list[tuple[int, float]]:
    """(chunk id, cosine distance), closest first."""
    distance = DocumentChunk.embedding.cosine_distance(embedding)
    rows = db.query(DocumentChunk.id, distance).filter(
        DocumentChunk.document_id == document_id
    ).order_by(distance, DocumentChunk.id).limit(n).all()
    return [(row[0], float(row[1])) for row in rows]


def _keyword_search(db: Session, document_id: int, text: str, n: int) -> list[int]:
    """Chunk ids matching ANY word of `text` (Postgres full-text, English stemming), best first."""
    words = re.findall(r"[A-Za-z0-9]+", text)  # alphanumerics only, so safe to build a tsquery from
    if not words:
        return []
    query = func.to_tsquery("english", " | ".join(words))
    rows = db.query(DocumentChunk.id).filter(
        DocumentChunk.document_id == document_id, DocumentChunk.tsv.op("@@")(query)
    ).order_by(func.ts_rank_cd(DocumentChunk.tsv, query).desc(), DocumentChunk.id).limit(n).all()
    return [row[0] for row in rows]


def _rrf(rankings: list[list[int]], weights: list[float] | None = None) -> list[int]:
    """Reciprocal Rank Fusion: score(id) = sum over rankings of weight / (RRF_K + rank)."""
    scores: dict[int, float] = defaultdict(float)
    for ranking, weight in zip(rankings, weights or [1.0] * len(rankings)):
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += weight / (RRF_K + rank)
    return sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))


def retrieve(db: Session, document_id: int, question: str, k: int = TOP_K,
             candidates: int = CANDIDATES) -> Retrieval:
    """Hybrid retrieval: pgvector cosine + Postgres full-text, fused with RRF.

    If the full question matches weakly and has several clauses, each clause is searched too
    and all rankings are fused. No LLM is involved.
    """
    embedding = embed_text(question)
    semantic = _semantic_search(db, document_id, embedding, candidates)
    keyword = _keyword_search(db, document_id, question, candidates)
    rankings = [_rrf([[i for i, _ in semantic], keyword])]
    seen_semantic = {i for i, _ in semantic}
    seen_keyword = set(keyword)

    clauses = split_clauses(question)
    best_similarity = 1 - semantic[0][1] if semantic else 0.0
    if len(clauses) > 1 and best_similarity < COMPOUND_TRIGGER:
        for clause in clauses:
            clause_semantic = _semantic_search(db, document_id, embed_text(clause), candidates)
            clause_keyword = _keyword_search(db, document_id, clause, candidates)
            rankings.append(_rrf([[i for i, _ in clause_semantic], clause_keyword]))
            seen_semantic.update(i for i, _ in clause_semantic)
            seen_keyword.update(clause_keyword)

    # The full question counts for more than any single clause, so clauses fill gaps rather than reorder
    top_ids = _rrf(rankings, [FULL_QUESTION_WEIGHT] + [1.0] * (len(rankings) - 1))[:k]
    if not top_ids:
        return Retrieval(hits=[], abstain=False)

    # Distance to the full question for every returned chunk, including keyword-only ones
    distance = DocumentChunk.embedding.cosine_distance(embedding)
    rows = db.query(DocumentChunk, distance).filter(DocumentChunk.id.in_(top_ids)).all()
    by_id = {chunk.id: (chunk, float(dist)) for chunk, dist in rows}

    hits = []
    for chunk_id in top_ids:
        chunk, dist = by_id[chunk_id]
        in_semantic, in_keyword = chunk_id in seen_semantic, chunk_id in seen_keyword
        matched_by = "both" if in_semantic and in_keyword else "semantic" if in_semantic else "keyword"
        hits.append(Hit(chunk=chunk, distance=dist, matched_by=matched_by))

    # Judged on the best semantic match overall, which fusion may have pushed out of the top k
    return Retrieval(hits=hits, abstain=best_similarity < ABSTAIN_SIMILARITY)


def build_context(chunks: list[DocumentChunk]) -> str:
    return "\n\n".join([f"[Chunk {chunk.chunk_index}]\n{chunk.chunk_text}" for chunk in chunks])
