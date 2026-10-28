"""/queries/ask when Groq fails, and how the Groq client is configured."""
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def document(client, make_user, fake_embeddings):
    user_id, headers = make_user("alice")
    r = client.post("/documents/upload", headers=headers, files={"file": ("a.txt", b"The tower is tall.")})
    assert r.status_code == 200
    return r.json()["id"], headers


def test_groq_failure_is_503_and_writes_no_query_log(client, document, monkeypatch):
    doc_id, headers = document

    def fail(question, context):
        raise ConnectionError("groq is down")

    monkeypatch.setattr("app.routers.queries.get_groq_answer", fail)
    r = client.post("/queries/ask", headers=headers, json={"document_id": doc_id, "question": "How tall?"})

    assert r.status_code == 503
    assert r.json()["detail"] == "The answer service is unavailable right now. Please try again shortly."
    assert "groq is down" not in r.text
    assert client.get("/queries/history", headers=headers).json() == []


def test_successful_ask_still_logs(client, document, monkeypatch):
    doc_id, headers = document
    monkeypatch.setattr("app.routers.queries.get_groq_answer", lambda question, context: "Tall.")
    r = client.post("/queries/ask", headers=headers, json={"document_id": doc_id, "question": "How tall?"})
    assert r.status_code == 200
    assert len(client.get("/queries/history", headers=headers).json()) == 1


def _groq_call(monkeypatch, env_model=None):
    """Run get_groq_answer against a mocked Groq class; returns (class mock, client mock)."""
    from app import groq_client
    if env_model:
        monkeypatch.setenv("GROQ_MODEL", env_model)
    else:
        monkeypatch.delenv("GROQ_MODEL", raising=False)
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [MagicMock(message=MagicMock(content="ok"))]
    groq_class = MagicMock()
    groq_class.return_value.__enter__.return_value = client
    monkeypatch.setattr(groq_client, "Groq", groq_class)
    assert groq_client.get_groq_answer("q", "ctx") == "ok"
    return groq_class, client


def test_groq_client_has_timeout_and_one_retry_and_is_closed(monkeypatch):
    groq_class, _ = _groq_call(monkeypatch)
    kwargs = groq_class.call_args.kwargs
    assert kwargs["timeout"] == 30
    assert kwargs["max_retries"] == 1
    groq_class.return_value.__exit__.assert_called_once()        # used as a context manager


def test_groq_model_defaults_and_comes_from_env(monkeypatch):
    _, client = _groq_call(monkeypatch)
    assert client.chat.completions.create.call_args.kwargs["model"] == "openai/gpt-oss-20b"
    _, client = _groq_call(monkeypatch, env_model="some/other-model")
    assert client.chat.completions.create.call_args.kwargs["model"] == "some/other-model"


def test_real_groq_client_supports_the_with_statement():
    from groq import Groq
    with Groq(api_key="not-a-real-key", timeout=30, max_retries=1) as client:
        assert client.max_retries == 1
