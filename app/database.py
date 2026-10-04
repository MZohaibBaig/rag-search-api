import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# create_all never alters an existing table, so schema additions made after the first
# deploy also live here as idempotent DDL. Keep each statement safe to re-run.
_SCHEMA_UPGRADES = [
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS ingest_version INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS tsv tsvector "
    "GENERATED ALWAYS AS (to_tsvector('english', chunk_text)) STORED",
    "CREATE INDEX IF NOT EXISTS ix_document_chunks_tsv ON document_chunks USING GIN (tsv)",
]


def init_db() -> None:
    """Create the pgvector extension, all tables and any schema upgrades. Idempotent."""
    # pgvector must exist before any table with a Vector column is created
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    # Imported here so every model is registered on Base before create_all runs
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for statement in _SCHEMA_UPGRADES:
            conn.execute(text(statement))

def get_db():
    """Dependency injection for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()