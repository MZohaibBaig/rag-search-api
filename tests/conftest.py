"""Test session setup: every test runs against a throwaway database, never the one in .env.

The database is created before any test module imports `app`, because app.database binds its engine
to DATABASE_URL at import time. When no Postgres is reachable the DB-backed fixtures skip, unless
REQUIRE_DB is set (CI sets it), in which case a missing database is a failure.
"""
import os
from unittest.mock import MagicMock

import pytest

from tests import scratch_db

_scratch = None
_UNAVAILABLE = scratch_db.unavailable_reason()


def pytest_configure(config):
    global _scratch
    if _UNAVAILABLE is None:
        _scratch = scratch_db.scratch_database("rag_tests")
        _scratch.__enter__()


def pytest_unconfigure(config):
    if _scratch is not None:
        _scratch.__exit__(None, None, None)


@pytest.fixture
def db_app():
    """The real FastAPI app (middleware, routers) on the scratch database, emptied for each test."""
    if _UNAVAILABLE:
        if os.environ.get("REQUIRE_DB"):
            pytest.fail(_UNAVAILABLE)
        pytest.skip(_UNAVAILABLE)
    from sqlalchemy import text
    from app import limits
    from app.database import engine
    from app.main import app

    def reset():
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE query_logs, document_chunks, documents, users RESTART IDENTITY CASCADE"))
        limits._hits.clear()

    reset()
    yield app
    reset()


@pytest.fixture
def client(db_app):
    from fastapi.testclient import TestClient
    return TestClient(db_app)


@pytest.fixture
def fake_embeddings(monkeypatch):
    """Every text embeds to the same unit vector (so every question is on topic), instantly."""
    vector = [1.0] + [0.0] * 383
    embed_batch = MagicMock(side_effect=lambda texts: [vector for _ in texts])
    monkeypatch.setattr("app.rag.embed_batch", embed_batch)
    monkeypatch.setattr("app.rag.embed_text", lambda text: vector)
    return embed_batch


@pytest.fixture
def make_user(db_app):
    """make_user("name") -> (user id, Authorization headers). Inserts the row directly: no bcrypt."""
    from app.auth import create_access_token
    from app.database import SessionLocal
    from app.models import User

    def make(username: str):
        with SessionLocal() as db:
            user = User(username=username, email=f"{username}@example.com", hashed_password="x")
            db.add(user)
            db.commit()
            return user.id, {"Authorization": f"Bearer {create_access_token(username)}"}
    return make
