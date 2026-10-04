from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import ProgrammingError

from app import demo

DOC = SimpleNamespace(id=1)
CHUNK = SimpleNamespace(chunk_index=0, chunk_text="The lighthouse was designed by Ada Marsh.")


def _missing_table():
    return ProgrammingError("SELECT ... FROM documents", {}, Exception('relation "documents" does not exist'))


@pytest.fixture
def client(monkeypatch):
    """Demo router with the DB layer mocked out: no Postgres, embeddings or Groq needed."""
    demo._hits.clear()
    monkeypatch.setattr(demo, "retrieve_chunks", lambda db, doc_id, q: [(CHUNK, 0.2)])
    monkeypatch.setattr(demo, "get_groq_answer", lambda q, ctx: "Ada Marsh.")
    app = FastAPI()
    app.include_router(demo.router)
    app.dependency_overrides[demo.get_db] = lambda: MagicMock()
    yield TestClient(app)
    demo._hits.clear()


def _ask(client):
    return client.post("/demo/ask", json={"question": "Who designed it?"})


def test_healthy_path_does_not_heal(client, monkeypatch):
    monkeypatch.setattr(demo, "find_demo_document", lambda db: DOC)
    init_db, seed = MagicMock(), MagicMock()
    monkeypatch.setattr(demo, "init_db", init_db)
    monkeypatch.setattr(demo, "seed_demo_document", seed)

    resp = _ask(client)

    assert resp.status_code == 200
    assert resp.json()["answer"] == "Ada Marsh."
    init_db.assert_not_called()
    seed.assert_not_called()


def test_missing_tables_are_recreated_and_request_succeeds(client, monkeypatch):
    # First lookup hits the wiped DB; after init_db + seed the document exists.
    state = {"healed": False}

    def find(db):
        if not state["healed"]:
            raise _missing_table()
        return DOC

    init_db = MagicMock()
    seed = MagicMock(side_effect=lambda: state.update(healed=True))
    monkeypatch.setattr(demo, "find_demo_document", find)
    monkeypatch.setattr(demo, "init_db", init_db)
    monkeypatch.setattr(demo, "seed_demo_document", seed)

    resp = _ask(client)

    assert resp.status_code == 200
    init_db.assert_called_once()
    seed.assert_called_once()


def test_missing_document_is_reseeded(client, monkeypatch):
    # Tables exist but the demo document is gone: query returns None until seeded.
    state = {"healed": False}
    monkeypatch.setattr(demo, "find_demo_document", lambda db: DOC if state["healed"] else None)
    monkeypatch.setattr(demo, "init_db", MagicMock())
    seed = MagicMock(side_effect=lambda: state.update(healed=True))
    monkeypatch.setattr(demo, "seed_demo_document", seed)

    assert _ask(client).status_code == 200
    seed.assert_called_once()


def test_still_failing_after_one_retry_returns_503_not_500(client, monkeypatch):
    find = MagicMock(side_effect=_missing_table())
    init_db, seed = MagicMock(), MagicMock()
    monkeypatch.setattr(demo, "find_demo_document", find)
    monkeypatch.setattr(demo, "init_db", init_db)
    monkeypatch.setattr(demo, "seed_demo_document", seed)

    resp = _ask(client)

    assert resp.status_code == 503
    init_db.assert_called_once()  # healed once, not in a loop
    seed.assert_called_once()


def test_init_db_failure_returns_503(client, monkeypatch):
    monkeypatch.setattr(demo, "find_demo_document", MagicMock(side_effect=_missing_table()))
    monkeypatch.setattr(demo, "init_db", MagicMock(side_effect=_missing_table()))
    seed = MagicMock()
    monkeypatch.setattr(demo, "seed_demo_document", seed)

    assert _ask(client).status_code == 503
    seed.assert_not_called()


def test_seed_that_swallows_its_error_still_gives_503(client, monkeypatch):
    # seed_demo_document logs and swallows failures, so the document is still missing afterwards.
    monkeypatch.setattr(demo, "find_demo_document", lambda db: None)
    monkeypatch.setattr(demo, "init_db", MagicMock())
    monkeypatch.setattr(demo, "seed_demo_document", MagicMock())

    assert _ask(client).status_code == 503
