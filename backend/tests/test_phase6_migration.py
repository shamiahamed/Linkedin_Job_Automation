"""Phase 6 — database migration verification (job_analysis / job_intelligence).

Uses a throwaway SQLite DB created through `main` (create_all + sqlite_migrate).
Verifies the SQLite PRAGMA migration adds the new columns, is idempotent, keeps
existing rows valid, and that the PostgreSQL `migrate()` path is a guarded no-op
on SQLite (never destructive). No database is deleted or recreated.
"""
import os
import tempfile
import unittest

from config import Config

# Switch app settings BEFORE importing main/database (same pattern as Phase 2-5).
_tmp = tempfile.mkdtemp(prefix="jobmig_")
Config.DATABASE_URL = "sqlite:///" + os.path.join(_tmp, "test.db").replace(os.sep, "/")
Config.API_TOKEN = ""
Config.APP_PASSWORD = ""
Config.GROQ_API_KEY = ""
Config.JOB_ANALYSIS_ENABLED = "false"
Config.JOB_INTELLIGENCE_ENABLED = "false"

from database import engine, SessionLocal  # noqa: E402
from models import Job, Application, Setting  # noqa: E402
from migrate import sqlite_migrate, migrate  # noqa: E402
import main as app_module  # noqa: E402  (runs create_all + SQLite migration)


def _columns(table):
    from sqlalchemy import text as _sql

    with engine.connect() as conn:
        rows = conn.execute(_sql("PRAGMA table_info(%s)" % table)).fetchall()
    return {r[1] for r in rows}


class MigrationTests(unittest.TestCase):
    def test_phase_2_and_4_columns_exist_after_startup(self):
        cols = _columns("jobs")
        self.assertIn("job_analysis", cols)
        self.assertIn("job_intelligence", cols)
        self.assertIn("fetch_batch", cols)
        self.assertIn("saved", cols)

    def test_migration_repeated_is_idempotent(self):
        sqlite_migrate(engine)
        sqlite_migrate(engine)
        cols = _columns("jobs")
        self.assertIn("job_analysis", cols)
        self.assertIn("job_intelligence", cols)

    def test_postgres_migrate_path_is_a_guarded_noop_on_sqlite(self):
        migrate(engine)  # dialect != postgresql -> returns without running ALTERs

    def test_existing_row_without_analysis_stays_valid(self):
        db = SessionLocal()
        try:
            db.query(Application).delete()
            db.query(Setting).delete()
            db.query(Job).delete()
            db.commit()
            # Simulate a pre-Phase-2 row: old columns only, nullable new ones.
            row = Job(title="Legacy Job", company="Acme", location="Chennai",
                      source="linkedin", emails=[], phones=[], status="pending")
            db.add(row)
            db.commit()
            db.expire_all()
            loaded = db.query(Job).filter(Job.id == row.id).first()
            self.assertIsNone(loaded.job_analysis)
            self.assertIsNone(loaded.job_intelligence)
            d = loaded.to_dict()
            self.assertIn("job_analysis", d)
            self.assertIn("job_intelligence", d)
            self.assertIsNone(d["job_analysis"])
            self.assertIsNone(d["job_intelligence"])
            # A later capture round can still write analysis onto the row.
            loaded.job_analysis = {"analysis_status": "ok", "llm_enriched": False}
            db.commit()
            db.expire_all()
            after = db.query(Job).filter(Job.id == row.id).first()
            self.assertEqual(after.job_analysis["analysis_status"], "ok")
        finally:
            db.close()

    def test_older_rows_survive_rerun_of_migration(self):
        db = SessionLocal()
        try:
            db.query(Job).delete()
            db.commit()
            db.add(Job(title="Survivor", company="Acme", source="linkedin",
                       emails=[], phones=[], status="pending"))
            db.commit()
            sqlite_migrate(engine)  # run migration ON TOP of existing rows
            count = db.query(Job).count()
            self.assertEqual(count, 1)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()