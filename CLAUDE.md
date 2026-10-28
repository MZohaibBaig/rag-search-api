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
| `GROQ_MODEL` | `app/groq_client.py` | Optional; defaults to `openai/gpt-oss-20b` |
| `CORS_ALLOWED_ORIGINS` | `app/main.py` | Comma-separated; defaults to `localhost:3000,localhost:5173` |

## Architecture

This is a FastAPI RAG (Retrieval-Augmented Generation) backend. The query flow: upload document → chunk text → embed chunks → store in pgvector → ask question → hybrid search (pgvector cosine + Postgres full-text, fused with RRF; `app/rag.py: retrieve`) → top 5 chunks → Groq LLM → answer. Off-topic questions abstain before the LLM call. Retrieval quality: `python -m tests.eval.run_eval`.

**Entry point:** `app/main.py` — creates all SQLAlchemy tables at startup, wires up CORS, and includes the three routers.

**Database (`app/database.py`):** SQLAlchemy 2.x with a single `SessionLocal` factory on a `NullPool` engine (no connection is held between requests). `get_db()` is a FastAPI dependency yielding a session. Tables are auto-created by `Base.metadata.create_all` on startup; there are no migration files.

**Models (`app/models.py`):** Four tables:
- `users` — username/email/bcrypt-hashed password
- `documents` — owned by a user, metadata only (no file stored)
- `document_chunks` — text + `Vector(384)` embedding (pgvector); section/sentence-aware chunks (~500 chars, 100 overlap) prefixed with their heading; `tsv` generated tsvector column (GIN) for keyword search
- `query_logs` — persists every Q&A pair per user+document

**Auth (`app/auth.py`):** JWT via `python-jose` (HS256). `get_current_user` is a FastAPI dependency that validates the Bearer token and returns the `User` ORM object. `SECRET_KEY` is read at module import — missing it raises `RuntimeError` immediately.

**Embeddings (`app/embeddings.py`):** fastembed (ONNX Runtime) `TextEmbedding`, lazy-loaded as a module-level singleton on first call, with `threads=1` and `enable_cpu_mem_arena=False`; `embed_batch` uses `batch_size=16`. Model: `all-MiniLM-L6-v2` → 384-dim vectors. No torch or sentence-transformers. In Docker, the model is baked into the image at build time (no network hit on first request).

**Uploads (`app/routers/documents.py`, `app/extract.py`, `app/limits.py`):** `POST /documents/upload` is a plain `def` that takes a module lock without blocking (a second concurrent upload gets 429). `extract.py` turns .txt/.csv/.pdf/.docx/.xlsx into text (parser libs imported lazily) and raises `IngestError` (an `HTTPException`) for 413/415/422. `limits.py` has the Content-Length guard middleware (413 over 6 MB before the body is parsed) and the in-memory per-IP limiter; the per-user (10 docs) and global (30 uploads / 24 h) caps are DB-backed in `documents.py`. Limits: 5 MB file, 300k chars, 300 chunks. `app/housekeeping.py` deletes user data older than 7 days at startup (never the demo user).

**LLM (`app/groq_client.py`):** Calls Groq's model (`GROQ_MODEL`, default `openai/gpt-oss-20b`; 30 s timeout, 1 retry, client closed after each call) with a system prompt that constrains answers to provided context. Context is the top-5 chunks concatenated with chunk index headers.

**Routers:**
- `app/routers/auth.py` — `POST /auth/register`, `POST /auth/login`, `GET /auth/me`
- `app/routers/documents.py` — `POST /documents/upload`, `GET /documents/`, `GET /documents/{id}`, `DELETE /documents/{id}`
- `app/routers/queries.py` — `POST /queries/ask`, `GET /queries/history`, `GET /queries/history/{document_id}`

All document and query endpoints are user-scoped (every query filters `user_id == current_user.id`).

**Docker build:** Multi-stage. Stage 1 (`builder`) installs packages and pre-downloads the fastembed model. Stage 2 (`final`) copies only the installed packages and model weights — no build tools. Non-root `appuser` is used at runtime. Runs `exec uvicorn` with one worker and `MALLOC_ARENA_MAX=2`.

**pgvector:** The `vector` extension must exist before SQLAlchemy creates tables. In Docker, `init.sql` runs `CREATE EXTENSION IF NOT EXISTS vector;` automatically. For local dev, run it manually in your Postgres instance.

## Tests

`py -3.13 -m pytest` (config in `pytest.ini`). Tests run against a throwaway Postgres database created by `tests/conftest.py` from `DATABASE_URL`; DB-backed tests skip when no Postgres is reachable, unless `REQUIRE_DB=1` (CI sets it). Embeddings and Groq are mocked, except the retrieval eval (`tests/eval`) and the Linux-only memory test, which use the real model. CI (`.github/workflows/tests.yml`) runs the suite on every PR and push to `main`. `tests/manual_smoke.py` is a separate manual script against a running API, not part of pytest.
# Working agreement

## 1. Think before coding
Don't assume. Don't hide confusion. Surface tradeoffs.
- State assumptions explicitly. If uncertain, ask rather than guess.
- Present multiple interpretations when genuinely ambiguous — don't silently pick one.
- Push back if a simpler approach exists.
- Stop when confused. Name what's unclear and ask.
- If a file or path I referenced doesn't exist, say so and stop — do not create it to make the instruction work.

## 2. Simplicity first
Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or configurability that wasn't requested.
- No error handling for impossible scenarios.
- If 200 lines could be 50, rewrite it.

Test: would a senior engineer call this overcomplicated? If yes, simplify.

## 3. Surgical changes
Touch only what you must.
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor what isn't broken.
- Match existing style even if you'd do it differently.
- Notice unrelated dead code? Mention it. Don't delete it.
- Remove imports/variables that YOUR change orphaned. Nothing else.

Test: every changed line traces directly to the request.

## 4. Goal-driven execution
Define success criteria, then loop until verified.
- "Add validation" → "write tests for invalid inputs, then make them pass"
- "Fix the bug" → "write a test reproducing it, then make it pass"
- For multi-step work, state the plan as: step → verify: check

## 5. Verify, don't claim
Never report success without running something that proves it.
- Report real command output, not what you expect the output to be.
- A build that "should work" isn't verified. Build it.
- If you can't verify something (no API key, no network, no test suite), say so plainly rather than implying it passed.
- If you measure something, say what you measured and when — stale measurements have caused real confusion here.

## Project conventions

**Environment**
- Windows. Terminal commands must be PowerShell, not bash.
- Python: use `py -3.13`. Bare `python` resolves to 3.8 on this machine and can't install pinned requirements.
- Working venv is `venv/`. `.venv/` is gitignored.

**Git**
- Never commit or push unless explicitly asked. Default to leaving changes staged or unstaged for review.
- Branch off `main`, commit, push, open PR, wait for green checks, merge, pull. `main` is protected.
- Before staging: check `git status`. Generated directories (`staticfiles/`, `.venv/`, build output) have been accidentally staged twice. Don't let it happen again.

**Docker**
- Tail long build output (`| Select-Object -Last 60`), don't dump it in full.
- When checking an image, verify you're looking at the tag you just built — stale tags from earlier builds have caused wrong conclusions here.
- These apps bind `0.0.0.0` on `$PORT` with a fallback. Don't hardcode ports.

**Deployment context**
- Each project deploys to Railway with its own PostgreSQL service.
- Railway Serverless: never add pooled/persistent DB connections, background tasks or timers. Keep embedding batch_size=16 with the arena off.
- PG18 volumes mount at `/var/lib/postgresql`, NOT `/var/lib/postgresql/data`. This exact mistake silently broke persistence once.