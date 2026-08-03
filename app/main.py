import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine, Base
from app.routers import auth, documents, queries

# Create tables
Base.metadata.create_all(bind=engine)

# Initialize FastAPI app
app = FastAPI(
    title="RAG Search API",
    description="Retrieval-Augmented Generation backend for document Q&A",
    version="1.0.0"
)

# CORS middleware. Auth is a Bearer token (not cookies), so credentialed
# cross-origin requests are never needed — allow_credentials stays False.
cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173"
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(queries.router)

@app.get("/")
def root():
    """Health check endpoint."""
    return {
        "message": "RAG Search API is running",
        "docs": "/docs"
    }