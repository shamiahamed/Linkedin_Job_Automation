"""Regression test — purge retention must handle timezone-aware timestamps.

Production runs PostgreSQL, which returns `jobs.created_at` as a timezone-aware
timestamptz. `purge_old_jobs` used to compare it against a naive
`datetime.utcnow() - timedelta(...)` cutoff, raising
`TypeError: can't compare offset-naive and offset-aware datetimes` — that made
every stale auto-fetch row "[purge-skip]" (never purged, log spam). SQLite never
showed the bug because it stores/returns naive datetimes.

This test drives the same retention logic on SQLite but with timezone-aware
`created_at` values (the closest stand-in for Postgres behaviour without one),
and asserts old rows are purged while recent rows are retained.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from config import Config

# Switch app settings BEFORE importing main/database (repo-wide convention).
# NOTE: do NOT flip the *_ENABLED flags here — Config is process-global at
# import time and the last module to import wins them (Phase 6 modules rely on
# analysis/intelligence staying "true" for the full suite).
_tmp = tempfile.mkdtemp(prefix="jobpurge_")
Config.DATABASE_URL = "sqlite:///" + os.path.join(_tmp, "test.db").replace(os.sep, "/")
Config.API_TOKEN = ""
Config.APP_PASSWORD = ""
Config.GROQ_API_KEY = ""

import main as app_module  # noqa: E402,F401  (create_all + SQLite migration)
from database import SessionLocal  # noqa: E402
from models import Job, Application, Setting  # noqa: E402
from routes.jobs import purge_old_jobs  # noqa: E402


class PurgeTimezoneTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.db.query(Application).delete()
        self.db.query(Setting).delete()
        self.db.query(Job).delete()
        self.db.commit()
        self.addCleanup(self.db.close)

    def _add(self, **kw):
        job = Job(
            title=kw.pop("title", "Junior Python Developer"),
            company="Acme Soft",
            location=kw.pop("location", "Chennai"),
            experience=kw.pop("experience", "Fresher"),
            saved=False,
            **kw,
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def test_aware_old_purged_and_recent_retained(self):
        now = datetime.now(timezone.utc)  # timezone-aware, like Postgres
        old_auto = self._add(source="auto_fetch", status="pending",
                             created_at=now - timedelta(days=3))
        old_unapplied = self._add(source="linkedin", status="ready_to_send",
                                  created_at=now - timedelta(days=20))
        recent_auto = self._add(source="auto_fetch", status="pending",
                                created_at=now - timedelta(hours=1))
        recent_unapplied = self._add(source="linkedin", status="pending",
                                     created_at=now - timedelta(hours=1))

        purged = purge_old_jobs(self.db)

        self.assertEqual(purged, 2)
        self.assertEqual({j.id for j in self.db.query(Job).all()},
                         {recent_auto.id, recent_unapplied.id})
        self.assertIsNone(self.db.get(Job, old_auto.id))
        self.assertIsNone(self.db.get(Job, old_unapplied.id))

    def test_fresh_aware_records_not_touched(self):
        now = datetime.now(timezone.utc)
        fresh_auto = self._add(source="auto_fetch", status="pending",
                               created_at=now - timedelta(minutes=5))
        fresh_ready = self._add(source="linkedin", status="ready_to_send",
                                created_at=now - timedelta(minutes=5))
        self.assertEqual(purge_old_jobs(self.db), 0)
        self.assertEqual({j.id for j in self.db.query(Job).all()},
                         {fresh_auto.id, fresh_ready.id})


if __name__ == "__main__":
    unittest.main()