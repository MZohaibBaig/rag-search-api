from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import demo
from app.rag import NO_ANSWER, Hit, Retrieval

CHUNK = SimpleNamespace(chunk_index=3, chunk_text="History and Construction\nOttoline Varga designed the tower.")


@pytest.fixture
def client(monkeypatch):
    demo._hits.clear()
    monkeypatch.setattr(demo, "find_demo_document", lambda db: SimpleNamespace(id=1))
    app = FastAPI()
    app.include_router(demo.router)
    app.dependency_overrides[demo.get_db] = lambda: MagicMock()
    yield TestClient(app)
    demo._hits.clear()


def _ask(client):
    return client.post("/demo/ask", json={"question": "Who designed it?"})


def test_response_keeps_its_shape_and_adds_matched_by(client, monkeypatch):
    monkeypatch.setattr(demo, "retrieve", lambda db, doc_id, q: Retrieval(
        hits=[Hit(chunk=CHUNK, distance=0.25, matched_by="keyword")], abstain=False))
    monkeypatch.setattr(demo, "get_groq_answer", lambda q, ctx: "Ottoline Varga.")

    body = _ask(client).json()

    assert {"status", "answer", "error", "chunks", "retrieve_ms", "generate_ms"} <= body.keys()
    assert body["status"] == "ok" and body["abstained"] is False
    assert body["chunks"] == [{"chunk_index": 3, "chunk_text": CHUNK.chunk_text, "distance": 0.25,
                               "similarity": 0.75, "matched_by": "keyword"}]


def test_abstention_answers_without_calling_the_llm(client, monkeypatch):
    monkeypatch.setattr(demo, "retrieve", lambda db, doc_id, q: Retrieval(
        hits=[Hit(chunk=CHUNK, distance=0.95, matched_by="semantic")], abstain=True))
    groq = MagicMock()
    monkeypatch.setattr(demo, "get_groq_answer", groq)

    resp = _ask(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and body["abstained"] is True
    assert body["answer"] == NO_ANSWER
    assert len(body["chunks"]) == 1  # the retrieved chunks are still shown
    groq.assert_not_called()
