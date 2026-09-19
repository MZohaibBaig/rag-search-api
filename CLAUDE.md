# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Local development (uvicorn + native Postgres):**
```bash
uvicorn app.main:app --reload
```
Requires `.env` with a `DATABASE_URL` pointing at a local Postgres that already has the `vector` extension enabled (`CREATE EXTENSION IF NOT EXISTS vector;`).

**Docker dev (full stack):**
```bash
docker compose up --build
```
DB is exposed on `127.0.0.1:5433` (not 5432, to avoid conflicts with a native Postgres). App on `:8000`.

**Docker prod:**
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```
The prod overlay (`docker-compose.prod.yml`) removes the bind mount and tags the image as `rag-search-api:prod`. Never use `docker-compose.override.yml` — that would auto-load and silently break dev.

**Generate a SECRET_KEY:**
```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```
`app/auth.py` crashes at import if `SECRET_KEY` is missing from the environment.

**Interactive API docs:** `http://localhost:8000/docs`

## Required environment variables

Copy `.env.example` to `.env`. Critical ones:

| Variable | Used by | Notes |
|---|---|---|
| `DATABASE_URL` | uvicorn (direct) | Points at your local Postgres; independent of `POSTGRES_PASSWORD` |
| `POSTGRES_PASSWORD` | docker-compose | Sets both the db container password and the app's `DATABASE_URL` inside compose |
| `SECRET_KEY` | `app/auth.py` | Required — startup will crash if absent |
| `GROQ_API_KEY` | `app/groq_client.py` | Groq API key for LLM calls |
| `EMBEDDING_MODEL` | `app/embeddings.py` | Defaults to `sentence-transformers/all-MiniLM-L6-v2`; model is baked into the Docker image |
| `CORS_ALLOWED_ORIGINS` | `app/main.py` | Comma-separated; defaults to `localhost:3000,localhost:5173` |

## Architecture

This is a FastAPI RAG (Retrieval-Augmented Generation) backend. The query flow: upload document → chunk text → embed chunks → store in pgvector → ask question → embed question → cosine similarity search → top 5 chunks → Groq LLM → answer.

**Entry point:** `app/main.py` — creates all SQLAlchemy tables at startup, wires up CORS, and includes the three routers.

**Database (`app/database.py`):** SQLAlchemy 2.x with a single `SessionLocal` factory. `get_db()` is a FastAPI dependency yielding a session. Tables are auto-created by `Base.metadata.create_all` on startup; there are no migration files.

**Models (`app/models.py`):** Four tables:
- `users` — username/email/bcrypt-hashed password
- `documents` — owned by a user, metadata only (no file stored)
- `document_chunks` — text + `Vector(384)` embedding (pgvector); chunk_size=500 chars, overlap=100
- `query_logs` — persists every Q&A pair per user+document

**Auth (`app/auth.py`):** JWT via `python-jose` (HS256). `get_current_user` is a FastAPI dependency that validates the Bearer token and returns the `User` ORM object. `SECRET_KEY` is read at module import — missing it raises `RuntimeError` immediately.

**Embeddings (`app/embeddings.py`):** `SentenceTransformer` is lazy-loaded as a module-level singleton on first call. Model: `all-MiniLM-L6-v2` → 384-dim vectors. In Docker, the model is baked into the image at build time (no network hit on first request).

**LLM (`app/groq_client.py`):** Calls Groq's `llama-3.1-8b-instant` with a system prompt that constrains answers to provided context. Context is the top-5 chunks concatenated with chunk index headers.

**Routers:**
- `app/routers/auth.py` — `POST /auth/register`, `POST /auth/login`, `GET /auth/me`
- `app/routers/documents.py` — `POST /documents/upload`, `GET /documents/`, `GET /documents/{id}`, `DELETE /documents/{id}`
- `app/routers/queries.py` — `POST /queries/ask`, `GET /queries/history`, `GET /queries/history/{document_id}`

All document and query endpoints are user-scoped (every query filters `user_id == current_user.id`).

**Docker build:** Multi-stage. Stage 1 (`builder`) installs packages and pre-downloads the embedding model. Torch CPU wheel is installed first explicitly to prevent pip from pulling the ~6 GB GPU wheel. Stage 2 (`final`) copies only the installed packages and model weights — no build tools. Non-root `appuser` is used at runtime.

**pgvector:** The `vector` extension must exist before SQLAlchemy creates tables. In Docker, `init.sql` runs `CREATE EXTENSION IF NOT EXISTS vector;` automatically. For local dev, run it manually in your Postgres instance.

## No tests

There are no application tests. A `test_endpoints.py` was renamed out of pytest's discovery path (see commit history).
