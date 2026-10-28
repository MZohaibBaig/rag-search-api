import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, exists, select

from app.database import SessionLocal
from app.demo import DEMO_USERNAME
from app.models import Document, DocumentChunk, QueryLog, User

logger = logging.getLogger("uvicorn.error")

RETENTION_DAYS = 7


def cleanup_old_data() -> tuple[int, int]:
    """Delete documents (with their chunks and query logs) uploaded more than RETENTION_DAYS ago,
    then users older than that who have no documents left. The demo user and its document are never
    touched. Runs once at startup (no timer, so an idle app can sleep). Returns (documents, users)."""
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
        stale_ids = [row[0] for row in db.execute(
            select(Document.id).join(User, Document.user_id == User.id)
            .where(User.username != DEMO_USERNAME, Document.uploaded_at < cutoff)
        )]
        if stale_ids:
            # The foreign keys have no ON DELETE CASCADE (the ORM does the cascading), so children go first
            db.execute(delete(DocumentChunk).where(DocumentChunk.document_id.in_(stale_ids)))
            db.execute(delete(QueryLog).where(QueryLog.document_id.in_(stale_ids)))
            db.execute(delete(Document).where(Document.id.in_(stale_ids)))
        users = db.execute(
            delete(User).where(
                User.username != DEMO_USERNAME,
                User.created_at < cutoff,
                ~exists().where(Document.user_id == User.id),
            )
        ).rowcount
        db.commit()
        if stale_ids or users:
            logger.info("Housekeeping removed %d document(s) and %d user(s) older than %d days",
                        len(stale_ids), users, RETENTION_DAYS)
        return len(stale_ids), users
    except Exception:
        db.rollback()
        logger.exception("Housekeeping failed; old data was left in place")
        return 0, 0
    finally:
        db.close()
