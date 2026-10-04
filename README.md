# RAG Search API

## Overview

A production-ready Retrieval-Augmented Generation (RAG) backend built with FastAPI, PostgreSQL + pgvector, and Groq LLM. Users upload plain-text documents; the system chunks and embeds them into a vector store, then answers questions by retrieving the most semantically relevant chunks and generating grounded answers via an LLM — all in a single API call.

## Live demo

https://zohaib-rag-search-api-demo.up.railway.app/demo — no signup required. The first question after idle takes ~20s while the model loads.

[API docs](https://zohaib-rag-search-api-demo.up.railway.app/docs) · [Health check](https://zohaib-rag-search-api-demo.up.railway.app/health)

## Why This Project

This project applies core AI/ML concepts — embeddings, vector similarity search, and LLM integration — to a real, deployable backend. It bridges my FYP work on visual search ([LensHive](https://github.com/Bee-code1/LensHive): CLIP + FAISS for image retrieval) to text-based RAG, demonstrating depth in retrieval systems and semantic understanding across both modalities.

## Architecture

```
Client Request
      │
      ▼
┌─────────────────────────────────────────────┐
│                  FastAPI App                │
│                                             │
│  ┌──────────┐ ┌───────────┐ ┌───────────┐  │
│  │  /auth   │ │/documents │ │ /queries  │  │
│  │ register │ │  upload   │ │    ask    │  │
│  │  login   │ │   list    │ │  history  │  │
│  │    me    │ │  delete   │ │           │  │
│  └────┬─────┘ └─────┬─────┘ └─────┬─────┘  │
│       │             │             │         │
│  JWT Auth      ┌────┴────┐   ┌────┴────┐   │
│  (bcrypt +     │Chunking │   │Question │   │
│   HS256)       │Embedding│   │Embedding│   │
│                └────┬────┘   └────┬────┘   │
└─────────────────────┼─────────────┼────────┘
                       │             │
              ┌────────▼─────────────▼────────┐
              │   PostgreSQL 18 + pgvector     │
              │  (cosine distance, top-5 ANN)  │
              └────────────────┬───────────────┘
                               │ retrieved chunks
                      ┌────────▼────────┐
                      │    Groq LLM     │
                      │ openai/gpt-oss- │
                      │      20b        │
                      └────────┬────────┘
                               │ grounded answer
                               ▼
                          API Response
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | FastAPI, Uvicorn |
| **Database** | PostgreSQL 18 + pgvector 0.8.3 |
| **Auth** | JWT (HS256) + bcrypt password hashing |
| **Embeddings** | fastembed (ONNX runtime), sentence-transformers/all-MiniLM-L6-v2 (384-dim) |
| **LLM** | Groq API — openai/gpt-oss-20b (free tier) |
| **Testing** | httpx (end-to-end script) |
| **Containerization** | Docker + docker-compose |

## Setup

### Local Development

**Prerequisites:** Python 3.13, PostgreSQL 18 with pgvector extension, a [Groq API key](https://console.groq.com).

1. Clone and enter the project:
   ```bash
   git clone https://github.com/MZohaibBaig/rag-search-api.git
   cd rag-search-api
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   py -3.13 -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Configure environment variables:
   ```bash
   cp .env.example .env
   # Edit .env — set GROQ_API_KEY, DATABASE_URL, and SECRET_KEY
   ```

4. Create the database and enable pgvector:
   ```bash
   psql -U postgres
   CREATE DATABASE rag_search_db;
   \c rag_search_db
   CREATE EXTENSION vector;
   \q
   ```

5. Start the API:
   ```bash
   uvicorn app.main:app --reload
   ```
   API available at `http://127.0.0.1:8000` — interactive docs at `/docs`.

### Docker (One-Command Setup)

```bash
docker-compose up --build
```

- PostgreSQL starts on `localhost:5432`
- API starts on `localhost:8000`
- Tables and extensions auto-initialize on first run

Set your Groq key, Postgres password, and JWT secret before running
(`docker-compose.yml` reads `POSTGRES_PASSWORD` and `SECRET_KEY` with no
default, so it fails immediately if either is unset):
```bash
# Windows PowerShell
$env:GROQ_API_KEY="gsk_..."
$env:POSTGRES_PASSWORD="change-me"
$env:SECRET_KEY="change-me"
docker-compose up --build

# Linux / macOS
GROQ_API_KEY="gsk_..." POSTGRES_PASSWORD="change-me" SECRET_KEY="change-me" docker-compose up --build
```

## API Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/auth/register` | No | Register (username, email, password) |
| `POST` | `/auth/login` | No | Login — returns JWT Bearer token |
| `GET` | `/auth/me` | Bearer | Current user info |
| `POST` | `/documents/upload` | Bearer | Upload text file → chunk → embed → store |
| `GET` | `/documents/` | Bearer | List user's documents |
| `GET` | `/documents/{id}` | Bearer | Document details with all chunks |
| `DELETE` | `/documents/{id}` | Bearer | Delete document (cascades chunks + logs) |
| `POST` | `/queries/ask` | Bearer | Ask a question (full RAG pipeline) |
| `GET` | `/queries/history` | Bearer | All past Q&A for user |
| `GET` | `/queries/history/{doc_id}` | Bearer | Q&A history for a specific document |

All protected endpoints expect `Authorization: Bearer <token>` in the request header.

## Testing

Run the end-to-end verification script (requires the API to be running):

```bash
python tests/manual_smoke.py
```

Run the pytest suite:

```bash
pytest
```

`tests/` covers chunking (section/sentence boundaries, overlap, size limit), RRF fusion, abstention, the demo endpoints (self-heal, rate limiting, retrieval response), and two Postgres-backed checks (schema upgrade/reseed and the retrieval eval thresholds), which are skipped when no Postgres is reachable. `manual_smoke.py` above is a separate manual script, not part of pytest.

The script registers a user, logs in, uploads a sample document, asks a question, and checks history — printing a `PASS/FAIL` result per step.

Expected output:
```
RAG Search API — End-to-End Test
Target: http://127.0.0.1:8000

  PASS  [200] Register user — id=1
  PASS  [200] Login — token received
  PASS  [200] GET /auth/me — username=testuser
  PASS  [200] Upload document — doc_id=1, chunks=5
  PASS  [200] List documents — 1 document(s)
  PASS  [200] Ask question — answer="RAG stands for..."
  PASS  [200] Query history — 1 record(s)

========================================
  Result: 7/7 steps passed
========================================
```

## Key Design Decisions

1. **User data isolation** — Every query is scoped to `current_user.id`. Users cannot read, query, or delete another user's documents or history, enforced at the database query level (not just at the route level).

2. **Chunking strategy** — Chunks of up to ~500 characters that follow section, paragraph and sentence boundaries, with up to 100 characters of whole-sentence overlap. Chunks never cross a heading, and each is prefixed with its section heading so the heading is embedded and searchable with the text.

3. **Hybrid retrieval** — pgvector cosine distance (`<=>`) and Postgres full-text search (a generated `tsvector` column with a GIN index) each return 20 candidates, fused with Reciprocal Rank Fusion into the top 5. Multi-clause questions that match weakly are also searched clause by clause. If nothing in the document is on topic (best cosine similarity below a threshold calibrated in `tests/eval`), the API answers "can't answer" without calling the LLM. Retrieval quality is measured by `python -m tests.eval.run_eval` (recall@5, MRR, abstain rate).

4. **LLM grounding** — The Groq system prompt instructs the model to answer *only* from the retrieved chunks. If the answer is not in the context, the model says so. This reduces hallucination and keeps responses honest about what the document actually contains.

5. **Separated modules** — `chunking.py`, `embeddings.py`, and `groq_client.py` are isolated from the route handlers. Each can be swapped, tested, or replaced independently (e.g., swap fastembed for OpenAI embeddings, or Groq for a local Ollama model) without touching the API layer.

## Deployment

Deployed on Railway as two services: the API (built from the `Dockerfile`) and its own Postgres service with the pgvector extension. Embeddings run through fastembed (ONNX) instead of PyTorch/sentence-transformers, which cut the Docker image from 8.76GB to 746MB.

## Future Improvements

- **Batch embedding** for large documents (currently sequential per chunk)
- **Query caching** via Redis to avoid re-embedding identical questions
- **Streaming responses** via Server-Sent Events so answers stream token-by-token
- **Fine-tuned embeddings** for domain-specific corpora (legal, medical, etc.)

## License

MIT
