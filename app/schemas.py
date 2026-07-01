from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional, List

# ============ User Schemas ============
class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    created_at: datetime
    
    class Config:
        from_attributes = True

# ============ Token Schema ============
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenPayload(BaseModel):
    sub: str  # username
    exp: int  # expiration unix timestamp

# ============ Document Schemas ============
class DocumentResponse(BaseModel):
    id: int
    filename: str
    uploaded_at: datetime
    
    class Config:
        from_attributes = True

class DocumentWithChunks(DocumentResponse):
    chunks: List['DocumentChunkResponse'] = []

# ============ Chunk Schemas ============
class DocumentChunkResponse(BaseModel):
    id: int
    chunk_index: int
    chunk_text: str
    created_at: datetime
    
    class Config:
        from_attributes = True

# ============ Query Schemas ============
class AskQuestion(BaseModel):
    document_id: int
    question: str

class QueryLogResponse(BaseModel):
    id: int
    question: str
    answer: str
    created_at: datetime
    
    class Config:
        from_attributes = True

class AskQuestionResponse(BaseModel):
    answer: str
    retrieved_chunks: List[DocumentChunkResponse]
    query_log_id: int