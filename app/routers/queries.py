from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.models import User, Document, DocumentChunk, QueryLog
from app.schemas import AskQuestion, AskQuestionResponse, QueryLogResponse, DocumentChunkResponse
from app.auth import get_current_user
from app.embeddings import embed_text
from app.groq_client import get_groq_answer

router = APIRouter(prefix="/queries", tags=["queries"])

@router.post("/ask", response_model=AskQuestionResponse)
def ask_question(
    payload: AskQuestion,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Ask a question about a document.
    
    Flow:
    1. Verify document belongs to current user.
    2. Embed the question.
    3. Search for most similar chunks via vector similarity (pgvector).
    4. Send top chunks + question to Groq.
    5. Store Q&A in QueryLog.
    6. Return answer + retrieved chunks.
    """
    # Verify document belongs to user
    document = db.query(Document).filter(
        Document.id == payload.document_id,
        Document.user_id == current_user.id
    ).first()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found or access denied"
        )
    
    # Embed the question
    question_embedding = embed_text(payload.question)
    
    # Retrieve top 5 most similar chunks using pgvector's <-> operator (cosine distance)
    # Note: pgvector uses <-> for distance (lower = more similar)
    top_chunks = db.query(DocumentChunk).filter(
        DocumentChunk.document_id == payload.document_id
    ).order_by(
        DocumentChunk.embedding.cosine_distance(question_embedding)
    ).limit(5).all()
    
    if not top_chunks:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No chunks found in document"
        )
    
    # Concatenate chunk texts to form context
    context = "\n\n".join([f"[Chunk {chunk.chunk_index}]\n{chunk.chunk_text}" for chunk in top_chunks])
    
    # Generate answer via Groq
    answer = get_groq_answer(payload.question, context)
    
    # Store in QueryLog
    query_log = QueryLog(
        user_id=current_user.id,
        document_id=payload.document_id,
        question=payload.question,
        answer=answer
    )
    db.add(query_log)
    db.commit()
    db.refresh(query_log)
    
    # Format chunk responses
    retrieved_chunks = [
        DocumentChunkResponse(
            id=chunk.id,
            chunk_index=chunk.chunk_index,
            chunk_text=chunk.chunk_text,
            created_at=chunk.created_at
        )
        for chunk in top_chunks
    ]
    
    return AskQuestionResponse(
        answer=answer,
        retrieved_chunks=retrieved_chunks,
        query_log_id=query_log.id
    )

@router.get("/history", response_model=list[QueryLogResponse])
def get_query_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all Q&A history for the current user."""
    query_logs = db.query(QueryLog).filter(
        QueryLog.user_id == current_user.id
    ).order_by(desc(QueryLog.created_at)).all()
    
    return query_logs

@router.get("/history/{document_id}", response_model=list[QueryLogResponse])
def get_document_query_history(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get Q&A history for a specific document (user-scoped)."""
    # Verify user owns document
    document = db.query(Document).filter(
        Document.id == document_id,
        Document.user_id == current_user.id
    ).first()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found or access denied"
        )
    
    query_logs = db.query(QueryLog).filter(
        QueryLog.document_id == document_id,
        QueryLog.user_id == current_user.id
    ).order_by(desc(QueryLog.created_at)).all()
    
    return query_logs