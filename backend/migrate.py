"""Idempotent ALTER TABLE additions for existing deployments.

SQLite/`create_all` recreates full schema on a fresh DB, but Neon Postgres keeps
the original table — columns added later in the app lifecycle never appear there,
and INSERTs of those columns then fail with 500. These run at every startup and
no-op if the columns already exist.
"""
import logging

from sqlalchemy import text

logger = logging.getLogger("uvicorn.error")

# (table, column, sql type) — columns added after the first production deploy.
_ADDITIONS = [
    ("jobs", "source", "VARCHAR(50)"),
    ("jobs", "apply_link", "VARCHAR(500)"),
    ("jobs", "has_email", "BOOLEAN"),
    ("jobs", "has_phone", "BOOLEAN"),
    ("jobs", "updated_at", "TIMESTAMPTZ DEFAULT now()"),
]

# (table, column, sql type) — widen existing columns (SQLite ignores lengths so
# this only matters on Postgres, where captures with long 'experience' text 500'd).
_ALTERS = [
    ("jobs", "experience", "VARCHAR(255)"),
]


def migrate(engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    try:
        with engine.begin() as conn:
            for table, column, sql_type in _ADDITIONS:
                conn.execute(
                    text(
                        f"ALTER TABLE {table} "
                        f"ADD COLUMN IF NOT EXISTS {column} {sql_type}"
                    )
                )
            for table, column, sql_type in _ALTERS:
                conn.execute(
                    text(
                        f"ALTER TABLE {table} "
                        f"ALTER COLUMN {column} TYPE {sql_type}"
                    )
                )
            conn.execute(
                text(
                    "UPDATE jobs SET updated_at = created_at "
                    "WHERE updated_at IS NULL"
                )
            )
        logger.info("Postgres migrations applied")
    except Exception:
        logger.exception("Postgres migration step failed (non-fatal)")