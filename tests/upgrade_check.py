"""Schema upgrade + reseed scenario, run in a throwaway database: python -m tests.upgrade_check

Simulates production as it is today (documents without ingest_version, chunks without tsv, a demo
document built by the old chunker) and checks that boot upgrades it in place and reseeds it.
Exits non-zero with a message on the first failed expectation.
"""
import sys

import sqlalchemy as sa

from tests.scratch_db import scratch_database


def check(condition: bool, message: str) -> None:
    if not condition:
        sys.exit(f"FAIL: {message}")
    print(f"ok: {message}")


def main() -> None:
    with scratch_database("rag_upgrade"):
        from app import demo
        from app.database import engine, init_db
        from app.embeddings import embed_text
        from app.rag import INGEST_VERSION, retrieve

        def scalar(sql: str, **params):
            with engine.connect() as conn:
                return conn.execute(sa.text(sql), params).scalar()

        # Build today's production shape: create the current schema, then strip what is new.
        init_db()
        with engine.begin() as conn:
            conn.execute(sa.text("DROP INDEX ix_document_chunks_tsv"))
            conn.execute(sa.text("ALTER TABLE document_chunks DROP COLUMN tsv"))
            conn.execute(sa.text("ALTER TABLE documents DROP COLUMN ingest_version"))
            user_id = conn.execute(sa.text(
                "INSERT INTO users (username, email, hashed_password, created_at) "
                "VALUES ('demo', 'demo@localhost.invalid', 'x', now()) RETURNING id")).scalar()
            doc_id = conn.execute(sa.text(
                "INSERT INTO documents (user_id, filename, uploaded_at) "
                "VALUES (:u, :f, now()) RETURNING id"), {"u": user_id, "f": demo.DEMO_FILENAME}).scalar()
            vector = "[" + ",".join(str(x) for x in embed_text("old style chunk")) + "]"
            conn.execute(sa.text(
                "INSERT INTO document_chunks (document_id, chunk_text, embedding, chunk_index, created_at) "
                "VALUES (:d, 'Ottoline Varga designed the tower.', CAST(:v AS vector), 0, now())"),
                {"d": doc_id, "v": vector})

        init_db()
        check(scalar("SELECT count(*) FROM information_schema.columns WHERE table_name='document_chunks' "
                     "AND column_name='tsv'") == 1, "init_db adds document_chunks.tsv to an existing table")
        check(scalar("SELECT count(*) FROM pg_indexes WHERE indexname='ix_document_chunks_tsv'") == 1,
              "init_db creates the GIN index")
        check(scalar("SELECT ingest_version FROM documents WHERE id=:d", d=doc_id) == 0,
              "existing documents get ingest_version 0")
        check(scalar("SELECT tsv IS NOT NULL FROM document_chunks WHERE document_id=:d", d=doc_id),
              "existing chunks are backfilled into tsv")
        init_db()
        check(True, "init_db is idempotent (third run raised nothing)")

        demo.seed_demo_document()
        docs = scalar("SELECT count(*) FROM documents WHERE filename=:f", f=demo.DEMO_FILENAME)
        version = scalar("SELECT ingest_version FROM documents WHERE filename=:f", f=demo.DEMO_FILENAME)
        check(docs == 1, "reseed leaves exactly one demo document (old copy removed)")
        check(version == INGEST_VERSION, f"demo document rebuilt at ingest version {INGEST_VERSION}")
        check(scalar("SELECT count(*) FROM documents WHERE id=:d", d=doc_id) == 0, "stale copy is gone")

        new_id = scalar("SELECT id FROM documents WHERE filename=:f", f=demo.DEMO_FILENAME)
        demo.seed_demo_document()
        check(scalar("SELECT id FROM documents WHERE filename=:f", f=demo.DEMO_FILENAME) == new_id,
              "seeding again is a no-op when the version matches")

        db = demo.SessionLocal()
        try:
            document = demo.find_demo_document(db)
            hits = retrieve(db, document.id, "Who designed the lighthouse?").hits
            check(len(hits) == 5, "hybrid retrieval returns 5 chunks from the reseeded document")
            check(all(h.matched_by in {"semantic", "keyword", "both"} for h in hits), "hits carry matched_by")
            check(any("Ottoline Varga" in h.chunk.chunk_text for h in hits), "answer-bearing chunk retrieved")
        finally:
            db.close()
            engine.dispose()


if __name__ == "__main__":
    main()
