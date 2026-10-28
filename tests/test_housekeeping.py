"""Startup cleanup of old user data, on a scratch database."""
from datetime import datetime, timedelta

OLD = timedelta(days=8)
RECENT = timedelta(days=2)


def seed(username: str, user_age: timedelta, doc_ages: list[timedelta]):
    """A user with one document (and one chunk + one query log) per age. Returns (user id, [doc ids])."""
    from app.database import SessionLocal
    from app.models import Document, DocumentChunk, QueryLog, User
    now = datetime.utcnow()
    with SessionLocal() as db:
        user = User(username=username, email=f"{username}@example.com", hashed_password="x",
                    created_at=now - user_age)
        db.add(user)
        db.flush()
        ids = []
        for age in doc_ages:
            doc = Document(user_id=user.id, filename=f"{username}.txt", uploaded_at=now - age)
            db.add(doc)
            db.flush()
            db.add(DocumentChunk(document_id=doc.id, chunk_text="text", embedding=[1.0] + [0.0] * 383, chunk_index=0))
            db.add(QueryLog(user_id=user.id, document_id=doc.id, question="q", answer="a"))
            ids.append(doc.id)
        db.commit()
        return user.id, ids


def counts() -> dict[str, int]:
    from sqlalchemy import func
    from app.database import SessionLocal
    from app.models import Document, DocumentChunk, QueryLog, User
    with SessionLocal() as db:
        return {model.__tablename__: db.query(func.count(model.id)).scalar()
                for model in (User, Document, DocumentChunk, QueryLog)}


def test_old_documents_are_deleted_with_chunks_and_logs(db_app):
    from app.housekeeping import cleanup_old_data
    _, (old_doc, recent_doc) = seed("alice", OLD, [OLD, RECENT])

    assert cleanup_old_data() == (1, 0)

    from app.database import SessionLocal
    from app.models import Document, DocumentChunk, QueryLog
    with SessionLocal() as db:
        assert [d.id for d in db.query(Document)] == [recent_doc]
        assert [c.document_id for c in db.query(DocumentChunk)] == [recent_doc]
        assert [q.document_id for q in db.query(QueryLog)] == [recent_doc]


def test_old_users_with_no_documents_are_deleted(db_app):
    from app.housekeeping import cleanup_old_data
    seed("stale", OLD, [OLD])            # loses its only document, then is itself removed
    seed("empty_old", OLD, [])           # never uploaded anything
    seed("empty_new", RECENT, [])        # too new to judge
    seed("keeper", OLD, [RECENT])        # old account, but still has a recent document

    assert cleanup_old_data() == (1, 2)

    from app.database import SessionLocal
    from app.models import User
    with SessionLocal() as db:
        assert sorted(u.username for u in db.query(User)) == ["empty_new", "keeper"]


def test_demo_user_and_document_are_never_touched(db_app):
    from app.housekeeping import cleanup_old_data
    seed("demo", timedelta(days=90), [timedelta(days=90)])
    seed("alice", OLD, [OLD])
    before = counts()

    assert cleanup_old_data() == (1, 1)

    after = counts()
    assert before == {"users": 2, "documents": 2, "document_chunks": 2, "query_logs": 2}
    assert after == {"users": 1, "documents": 1, "document_chunks": 1, "query_logs": 1}
    from app.database import SessionLocal
    from app.models import User
    with SessionLocal() as db:
        assert db.query(User).one().username == "demo"


def test_nothing_to_clean_is_a_no_op(db_app):
    from app.housekeeping import cleanup_old_data
    seed("alice", RECENT, [RECENT])
    assert cleanup_old_data() == (0, 0)
    assert counts()["documents"] == 1


def test_a_failure_is_logged_not_raised(db_app, monkeypatch):
    from app import housekeeping

    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr(housekeeping, "SessionLocal", Boom)
    assert housekeeping.cleanup_old_data() == (0, 0)       # startup must survive a cleanup failure
