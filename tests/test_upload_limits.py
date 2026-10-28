"""Caps and guards around /documents/upload and /auth/register. Real app, scratch database, fake embeddings."""
import threading
from datetime import datetime, timedelta

from tests.make_files import make_pdf

MB = 1024 * 1024


def add_documents(user_id: int, count: int, age: timedelta = timedelta(0)):
    from app.database import SessionLocal
    from app.models import Document
    with SessionLocal() as db:
        db.add_all(Document(user_id=user_id, filename=f"seed{i}.txt", uploaded_at=datetime.utcnow() - age)
                   for i in range(count))
        db.commit()


def upload(client, headers, name="n.txt", content=b"Some text"):
    return client.post("/documents/upload", headers=headers, files={"file": (name, content)})


# ---- DB-backed caps ----

def test_eleventh_document_is_refused(client, make_user, fake_embeddings):
    user_id, headers = make_user("alice")
    add_documents(user_id, 10)
    r = upload(client, headers)
    assert r.status_code == 403
    assert "10" in r.json()["detail"]
    fake_embeddings.assert_not_called()


def test_tenth_document_is_accepted(client, make_user, fake_embeddings):
    user_id, headers = make_user("alice")
    add_documents(user_id, 9)
    assert upload(client, headers).status_code == 200


def test_deleting_a_document_frees_a_slot(client, make_user, fake_embeddings):
    user_id, headers = make_user("alice")
    add_documents(user_id, 10)
    doc_id = client.get("/documents/", headers=headers).json()[0]["id"]
    assert client.delete(f"/documents/{doc_id}", headers=headers).status_code == 204
    assert upload(client, headers).status_code == 200


def test_global_daily_cap_is_429(client, make_user, fake_embeddings):
    _, headers = make_user("alice")
    for i in range(6):                                  # 6 users x 5 docs = 30 uploads in the last 24 h
        other_id, _ = make_user(f"user{i}")
        add_documents(other_id, 5)
    r = upload(client, headers)
    assert r.status_code == 429
    assert "daily upload limit" in r.json()["detail"]
    fake_embeddings.assert_not_called()


def test_global_cap_is_rolling_and_ignores_the_demo_user(client, make_user, fake_embeddings):
    _, headers = make_user("alice")
    old_id, _ = make_user("old")
    demo_id, _ = make_user("demo")
    add_documents(old_id, 10, age=timedelta(hours=25))   # outside the window
    add_documents(demo_id, 10)                            # the seeded demo document is not a user upload
    for i in range(2):
        other_id, _ = make_user(f"user{i}")
        add_documents(other_id, 9)                        # 18 recent uploads: under the cap
    assert upload(client, headers).status_code == 200


# ---- one ingest at a time ----

def test_parallel_upload_gets_429_while_one_is_running(client, make_user, monkeypatch):
    _, headers = make_user("alice")
    started, release = threading.Event(), threading.Event()

    def slow_embed(texts):
        started.set()
        assert release.wait(timeout=10)
        return [[1.0] + [0.0] * 383 for _ in texts]

    monkeypatch.setattr("app.rag.embed_batch", slow_embed)
    first = {}
    worker = threading.Thread(target=lambda: first.update(response=upload(client, headers, "first.txt")))
    worker.start()
    try:
        assert started.wait(timeout=10), "first upload never reached embedding"
        second = upload(client, headers, "second.txt")
        assert second.status_code == 429
        assert second.json()["detail"] == "Another upload is in progress, try again shortly"
    finally:
        release.set()
        worker.join(timeout=10)
    assert first["response"].status_code == 200
    assert upload(client, headers, "third.txt").status_code == 200      # the lock was released


def test_lock_is_released_after_a_failed_upload(client, make_user, fake_embeddings):
    _, headers = make_user("alice")
    assert upload(client, headers, "x.png", b"png").status_code == 415
    assert upload(client, headers, "ok.txt").status_code == 200


# ---- upload size guard (middleware) ----

def test_content_length_over_6mb_is_413_before_the_body_is_parsed(client, make_user, monkeypatch):
    _, headers = make_user("alice")

    def must_not_run(*args, **kwargs):
        raise AssertionError("the body was parsed")

    monkeypatch.setattr("app.routers.documents.extract_text", must_not_run)
    r = upload(client, headers, "big.txt", b"a" * (6 * MB))
    assert r.status_code == 413
    assert "5 MB" in r.json()["detail"]


def test_request_without_content_length_is_411(client, make_user):
    _, headers = make_user("alice")
    r = client.post("/documents/upload", headers={**headers, "Content-Type": "multipart/form-data; boundary=x"},
                    content=iter([b"--x--\r\n"]))        # an iterator body is sent chunked, with no length
    assert r.status_code == 411


def test_413_from_the_guard_carries_cors_headers(client):
    r = client.post("/documents/upload", headers={"Origin": "http://localhost:3000"},
                    files={"file": ("big.txt", b"a" * (6 * MB))})
    assert r.status_code == 413
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_guard_ignores_other_routes(client):
    assert client.get("/robots.txt").status_code == 200


# ---- per-IP limits ----

def test_register_is_limited_to_5_per_hour_per_ip(client):
    bad = {"username": "u", "email": "not-an-email", "password": "p"}      # 422, but still counted
    assert [client.post("/auth/register", json=bad).status_code for _ in range(5)] == [422] * 5
    r = client.post("/auth/register", json=bad)
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_upload_is_limited_to_10_per_hour_per_ip(client, make_user, fake_embeddings):
    _, headers = make_user("alice")
    assert [upload(client, headers, "x.png", b"png").status_code for _ in range(10)] == [415] * 10
    assert upload(client, headers).status_code == 429


def test_ip_limits_are_independent_per_address(client, make_user, fake_embeddings):
    _, headers = make_user("alice")
    for _ in range(10):
        upload(client, headers, "x.png", b"png")
    elsewhere = client.post("/documents/upload", headers={**headers, "X-Forwarded-For": "7.7.7.7"},
                            files={"file": ("ok.txt", b"Some text")})
    assert elsewhere.status_code == 200


# ---- misc ----

def test_database_engine_holds_no_pooled_connections():
    from sqlalchemy.pool import NullPool
    from app.database import engine
    assert isinstance(engine.pool, NullPool)


def test_robots_txt_disallows_everything(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.text == "User-agent: *\nDisallow: /"
    assert r.headers["content-type"].startswith("text/plain")


def test_a_real_pdf_round_trips_through_ask(client, make_user, fake_embeddings, monkeypatch):
    _, headers = make_user("alice")
    doc = upload(client, headers, "fact.pdf", make_pdf(["Ottoline Varga designed the tower"])).json()
    monkeypatch.setattr("app.routers.queries.get_groq_answer", lambda question, context: "Ottoline Varga.")
    r = client.post("/queries/ask", headers=headers, json={"document_id": doc["id"], "question": "Who designed it?"})
    assert r.status_code == 200
    assert r.json()["answer"] == "Ottoline Varga."
