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
    ("jobs", "saved", "BOOLEAN"),
    ("jobs", "fetch_batch", "VARCHAR(50)"),
    ("jobs", "job_analysis", "JSON"),
    ("jobs", "job_intelligence", "JSON"),
    ("jobs", "updated_at", "TIMESTAMPTZ DEFAULT now()"),
    ("applications", "follow_up_at", "TIMESTAMPTZ"),
    ("applications", "followed_up_at", "TIMESTAMPTZ"),
    ("applications", "outcome", "VARCHAR(20)"),
    ("applications", "notes", "TEXT"),
    ("push_subscriptions", "p256dh", "VARCHAR(255)"),
    ("push_subscriptions", "auth", "VARCHAR(255)"),
    ("push_subscriptions", "created_at", "TIMESTAMPTZ DEFAULT now()"),
]

# (table, column, sql type) — widen existing columns (SQLite ignores lengths so
# this only matters on Postgres, where captures with long 'experience' text 500'd).
_ALTERS = [
    ("jobs", "experience", "VARCHAR(255)"),
]


def sqlite_migrate(engine) -> None:
    """Idempotent ALTER TABLE additions for existing SQLite databases.

    Runs at every startup (via main.py) and no-ops once each column exists.
    Mirrors the Postgres additions above so both dialects converge on the same
    final schema. Failures are swallowed — create_all already built the full
    schema on fresh databases, so a cold DB is safe to keep booting regardless.
    """
    from sqlalchemy import text as _sql

    try:
        with engine.connect() as _conn:
            _cols = [r[1] for r in _conn.execute(_sql("PRAGMA table_info(jobs)")).fetchall()]
            for _col, _def in (("apply_link", "TEXT"), ("updated_at", "DATETIME"),
                               ("saved", "BOOLEAN"), ("fetch_batch", "TEXT"),
                               ("job_analysis", "JSON"),
                               ("job_intelligence", "JSON")):
                if _col not in _cols:
                    _conn.execute(_sql(f"ALTER TABLE jobs ADD COLUMN {_col} {_def}"))
            _conn.execute(_sql("UPDATE jobs SET updated_at = created_at WHERE updated_at IS NULL"))

            _acols = [r[1] for r in _conn.execute(_sql("PRAGMA table_info(applications)")).fetchall()]
            for _col, _def in (("follow_up_at", "DATETIME"), ("followed_up_at", "DATETIME"),
                               ("outcome", "VARCHAR(20)"), ("notes", "TEXT")):
                if _col not in _acols:
                    _conn.execute(_sql(f"ALTER TABLE applications ADD COLUMN {_col} {_def}"))

            _conn.execute(_sql(
                "CREATE TABLE IF NOT EXISTS push_subscriptions ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "endpoint VARCHAR(500) NOT NULL UNIQUE, "
                "p256dh VARCHAR(255) NOT NULL, "
                "auth VARCHAR(255) NOT NULL, "
                "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
            ))
            _conn.commit()
    except Exception:
        pass


def migrate(engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS push_subscriptions ("
                    "id SERIAL PRIMARY KEY, "
                    "endpoint VARCHAR(500) NOT NULL UNIQUE, "
                    "p256dh VARCHAR(255) NOT NULL, "
                    "auth VARCHAR(255) NOT NULL, "
                    "created_at TIMESTAMPTZ DEFAULT now())"
                )
            )
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