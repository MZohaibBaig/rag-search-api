import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.database import engine, Base, SessionLocal
from app.routers import auth, documents, queries
from app import demo

# Create tables
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    demo.seed_demo_document()
    yield


# Initialize FastAPI app
app = FastAPI(
    lifespan=lifespan,
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
app.include_router(demo.router)


@app.get("/")
def root():
    """Health check endpoint."""
    return {
        "message": "RAG Search API is running",
        "docs": "/docs"
    }


@app.get("/health", include_in_schema=False)
def health():
    """Unauthenticated liveness + DB connectivity check. Excluded from OpenAPI schema."""
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except SQLAlchemyError as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "detail": str(exc)},
        )
    finally:
        db.close()