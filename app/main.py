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

# CORS middleware (allow frontend to call from different origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to your frontend domain
    allow_credentials=True,
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