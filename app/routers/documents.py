from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.models import User, Document
from app.schemas import DocumentResponse, DocumentWithChunks
from app.auth import get_current_user
from app.rag import ingest_text
from app.auth import get_current_user
from app.groq_client import get_groq_answer

router = APIRouter(prefix="/documents", tags=["documents"])

@router.post("/upload", response_model=DocumentWithChunks)
async def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Upload a text document, chunk it, embed it, and store in database.
    """
    # Read file content
    content = await file.read()
    text = content.decode("utf-8", errors="ignore")
    
    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is empty"
        )
    
    return ingest_text(db, current_user.id, file.filename, text)

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