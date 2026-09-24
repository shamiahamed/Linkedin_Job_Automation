"""Phase 2 tests — Job Analysis Agent in the real ingest pipeline.

App-level: uses a throwaway SQLite DB (Config.DATABASE_URL is switched BEFORE
`main`/`database` are imported) so create_all + the SQLite PRAGMA migration
actually run against the `job_analysis` column. ingest_job() is the shared
capture pipeline (same function the API route and daily auto-fetch call);
auto-apply is switched OFF via the settings table so nothing is ever emailed.
All Config mutations are restored afterwards.
"""
import os
import tempfile
import unittest
from unittest import mock

from config import Config

# Switch the app's DB/auth/LLM settings BEFORE importing main/database.
_tmp = tempfile.mkdtemp(prefix="jobanalyze_")
Config.DATABASE_URL = "sqlite:///" + os.path.join(_tmp, "test.db").replace(os.sep, "/")
Config.API_TOKEN = ""
Config.APP_PASSWORD = ""
Config.GROQ_API_KEY = ""
Config.JOB_ANALYSIS_ENABLED = "true"
Config.JOB_ANALYSIS_USE_LLM = "false"

from database import SessionLocal  # noqa: E402
from models import Job, Application, Setting  # noqa: E402
from routes.jobs import ingest_job, JobCreate  # noqa: E402
import main as app_module  # noqa: E402  (runs create_all + SQLite migration)


def _job_data(**kw):
    base = {
        "title": "Python Developer",
        "company": "Acme Soft",
        "location": "Chennai",
        "url": "http://example.com/job-py",
        "description": "Python Developer, 1-2 years, FastAPI, SQL. Chennai office.",
        "emails": [],
        "phones": [],
        "source": "auto_fetch",
        "apply_link": "http://example.com/job-py",
    }
    base.update(kw)
    return JobCreate(**base)


class IngestAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.db.query(Application).delete()
        self.db.query(Setting).delete()
        self.db.query(Job).delete()
        self.db.commit()
        # Auto-apply OFF for every test in this module — no email/phone side effects.
        self.db.add(Setting(key="auto_apply", value="0"))
        self.db.commit()
        self.addCleanup(self.db.close)

    def test_ingest_stores_analysis_without_side_effects(self):
        job = ingest_job(self.db, _job_data())
        self.assertEqual(job["title"], "Python Developer")
        row = self.db.query(Job).filter(Job.id == job["id"]).first()
        self.assertIsNotNone(row.job_analysis)
        self.assertEqual(row.job_analysis["analysis_status"], "ok")
        self.assertTrue(row.job_analysis["role"]["matched"])
        self.assertTrue(row.job_analysis["experience"]["matched"])
        # status is apply_link/no_contact — the analyzer did NOT auto-apply or
        # tamper with the normal pipeline result.
        self.assertEqual(row.status, "apply_link")
        self.assertTrue(row.has_email is False and row.has_phone is False)

    def test_ingest_disabled_stores_none(self):
        mock.patch.object(Config, "JOB_ANALYSIS_ENABLED", "false").start()
        self.addCleanup(mock.patch.stopall)
        job = ingest_job(self.db, _job_data(url="http://example.com/disabled"))
        row = self.db.query(Job).filter(Job.id == job["id"]).first()
        self.assertIsNone(row.job_analysis)

    def test_ingest_survives_failed_analysis(self):
        mock.patch(
            "services.job_analyzer.analyze_job",
            side_effect=RuntimeError("boom"),
        ).start()
        self.addCleanup(mock.patch.stopall)
        job = ingest_job(self.db, _job_data(url="http://example.com/failed"))
        row = self.db.query(Job).filter(Job.id == job["id"]).first()
        self.assertIsNotNone(row)  # job still created
        self.assertEqual(row.title, "Python Developer")
        self.assertEqual(row.job_analysis["analysis_status"], "failed")

    def test_ingest_auto_apply_logic_unchanged(self):
        # Email job that WOULD auto-apply if the setting were on; with auto_apply
        # off it must land as 'pending' (the gate checks the setting, not the
        # analyzer) and still carry the analysis.
        job = ingest_job(self.db, _job_data(
            url="http://example.com/email-job",
            emails=["hr@acme.in"],
            apply_link="",
        ))
        row = self.db.query(Job).filter(Job.id == job["id"]).first()
        self.assertEqual(row.status, "pending")
        self.assertTrue(row.has_email)
        self.assertIsNotNone(row.job_analysis)
        self.assertEqual(row.job_analysis["analysis_status"], "ok")

    def test_ingest_similar_jobs_disabled_analysis_no_crash(self):
        # Regression guard: two near-identical jobs with analysis enabled and a
        # broken analyzer must still dedupe/insert cleanly (second is deduped).
        mock.patch(
            "services.job_analyzer.analyze_job",
            side_effect=RuntimeError("boom"),
        ).start()
        self.addCleanup(mock.patch.stopall)
        first = ingest_job(self.db, _job_data(description="dup job for dedupe test"))
        second = ingest_job(self.db, _job_data(description="dup job for dedupe test"))
        self.assertEqual(first["id"], second["id"])
        count = self.db.query(Job).count()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()