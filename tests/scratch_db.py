"""Throwaway Postgres databases for DB-backed checks (eval, schema upgrade).

Needs a Postgres with pgvector and a role allowed to CREATE DATABASE, reachable via the
DATABASE_URL env var (or .env). The scratch database is created next to the configured one,
so the real database is never touched.
"""
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def base_url() -> str:
    url = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (env or .env)")
    return url


def unavailable_reason() -> str | None:
    """Why a scratch database can't be created here, or None if it can."""
    try:
        engine = sa.create_engine(re.sub(r"/[^/]+$", "/postgres", base_url()))
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        return None
    except (SystemExit, Exception) as exc:
        return f"no Postgres available ({type(exc).__name__})"


@contextmanager
def scratch_database(prefix: str):
    """Create a database, point DATABASE_URL at it, and drop it on exit.

    Must run before app.database is imported: its engine binds to DATABASE_URL at import time,
    and a pre-imported engine would silently write to the real database.
    """
    if "app.database" in sys.modules:
        raise RuntimeError("scratch_database() must run in a fresh process: app.database is already imported")
    base = base_url()
    name = f"{prefix}_{os.getpid()}"
    admin = sa.create_engine(re.sub(r"/[^/]+$", "/postgres", base), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    os.environ["DATABASE_URL"] = re.sub(r"/[^/]+$", f"/{name}", base)
    try:
        yield os.environ["DATABASE_URL"]
    finally:
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
