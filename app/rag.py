from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk
from app.chunking import chunk_text
from app.embeddings import embed_text, embed_batch


def ingest_text(db: Session, user_id: int, filename: str, text: str) -> Document:
    """Create a document, chunk + embed its text, and store the chunks."""
    document = Document(user_id=user_id, filename=filename)
    db.add(document)
    db.commit()
    db.refresh(document)

    chunks = chunk_text(text)
    embeddings = embed_batch(chunks)

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


def retrieve_chunks(db: Session, document_id: int, question: str, k: int = 5):
    """Top-k chunks by cosine distance (lower = more similar), as (chunk, distance) pairs."""
    question_embedding = embed_text(question)
    distance = DocumentChunk.embedding.cosine_distance(question_embedding).label("distance")
    rows = db.query(DocumentChunk, distance).filter(
        DocumentChunk.document_id == document_id
    ).order_by(distance).limit(k).all()
    return [(chunk, float(dist)) for chunk, dist in rows]


def build_context(chunks: list[DocumentChunk]) -> str:
    return "\n\n".join([f"[Chunk {chunk.chunk_index}]\n{chunk.chunk_text}" for chunk in chunks])
