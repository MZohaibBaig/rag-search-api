# ── Stage 1: builder ──────────────────────────────────────────────────────────
# Install Python packages and pre-download the embedding model here so the
# final image needs zero outbound network access on first request.
FROM python:3.13-slim AS builder

WORKDIR /build

# Build deps for psycopg2-binary (provides its own libpq but needs gcc at wheel-
# compile time if a pre-built wheel isn't available for the platform).
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONPATH=/install/lib/python3.13/site-packages

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Pre-download the embedding model into a fixed path inside the image.
# SENTENCE_TRANSFORMERS_HOME tells the library where to store / find models.
# The model name matches the default in app/embeddings.py and the value set
# in docker-compose.yml so overriding EMBEDDING_MODEL at runtime is still
# supported — but the baked model is used when the env var is absent or
# matches this name.
ENV FASTEMBED_CACHE_PATH=/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2', cache_dir='/models')"


# ── Stage 2: final production image ───────────────────────────────────────────
FROM python:3.13-slim AS final

# postgresql-client is NOT needed at runtime; psycopg2-binary bundles libpq.
# The builder needed gcc/libpq-dev; the final image needs neither.

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy baked model weights from builder
COPY --from=builder /models /models

WORKDIR /app

# Non-root user
RUN useradd --no-create-home --no-log-init --system appuser \
    && chown appuser /app

COPY --chown=appuser . .

USER appuser

# Tell sentence-transformers to find the baked model at this path
ENV FASTEMBED_CACHE_PATH=/models

EXPOSE 8000

# Item 2: bind 0.0.0.0 on $PORT, fallback 8000 — matches existing compose
CMD sh -c "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"
