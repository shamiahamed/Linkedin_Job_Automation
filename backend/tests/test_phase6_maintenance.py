"""Phase 6 — double-fetch prevention + housekeeping + readiness endpoint.

Verifies the web process's scheduled loop:
  - TEMPORAL_ADDRESS set     -> the built-in external fetch is SKIPPED (the
                               Temporal worker Schedule owns it) but purge,
                               no-contact cleanup, and daily reminders STILL run.
  - TEMPORAL_ADDRESS absent  -> the built-in scheduler continues (fetch runs
                               at most once per day, gated by auto_fetch + the
                               last_fetch_at stamp).
Also verifies the auth-gated /api/readiness diagnostic reports booleans only
(no secret values) and never connects to a live Temporal Cloud account.
"""
import os
import tempfile
import unittest
from unittest import mock
from datetime import datetime

from config import Config

# Switch app settings BEFORE importing main/database.
_tmp = tempfile.mkdtemp(prefix="jobmaint_")
Config.DATABASE_URL = "sqlite:///" + os.path.join(_tmp, "test.db").replace(os.sep, "/")
Config.API_TOKEN = ""
Config.APP_PASSWORD = ""
Config.GROQ_API_KEY = ""
Config.TEMPORAL_ADDRESS = ""
Config.JOB_ANALYSIS_ENABLED = "true"
Config.JOB_INTELLIGENCE_ENABLED = "true"

from database import SessionLocal  # noqa: E402
from models import Job, Application, Setting  # noqa: E402
from temporal_worker import temporal_configured  # noqa: E402
import main as app_module  # noqa: E402


class DoubleFetchPreventionTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.db.query(Application).delete()
        self.db.query(Setting).delete()
        self.db.query(Job).delete()
        self.db.add(Setting(key="auto_apply", value="0"))
        self.db.add(Setting(key="auto_fetch", value="1"))
        self.db.commit()
        self.addCleanup(self.db.close)
        self._old = Config.TEMPORAL_ADDRESS
        self.addCleanup(setattr, Config, "TEMPORAL_ADDRESS", self._old)
        # Reminders must never hit the network in these tests — either they are
        # mocked, or (in the fetch-only tests) suppressed via reminders_due.
        self._due = mock.patch("services.reminders.reminders_due", return_value=False)
        self._due.start()
        self.addCleanup(self._due.stop)

    def test_temporal_configured_skips_fetch_but_keeps_housekeeping(self):
        Config.TEMPORAL_ADDRESS = "ns.acct.tmprl.cloud:7233"
        self.assertTrue(temporal_configured())
        self.assertTrue(app_module.temporal_handles_fetch())
        with mock.patch("services.reminders.reminders_due", return_value=True), \
             mock.patch("services.reminders.run_daily_reminders") as run, \
             mock.patch("services.reminders.mark_reminders_done") as done, \
             mock.patch("routes.jobs.fetch_jobs_now") as fetch:
            result = app_module.run_scheduled_maintenance(self.db)
        self.assertEqual(result["fetch_skipped_temporal"], True)
        self.assertEqual(result["fetch_attempted"], False)
        self.assertEqual(result["reminders_ran"], True)
        fetch.assert_not_called()
        run.assert_called_once_with(self.db)
        done.assert_called_once_with(self.db)
        # purge + no-contact cleanup still executed on this tick.
        self.assertIsNotNone(result["purged"])
        self.assertIsNotNone(result["no_contact_cleaned"])

    def test_no_temporal_fetch_attempted_when_already_fetched_today(self):
        Config.TEMPORAL_ADDRESS = ""
        self.db.add(Setting(key="last_fetch_at",
                            value=datetime.utcnow().date().isoformat() + "T00:00:00"))
        self.db.commit()
        with mock.patch("routes.jobs.fetch_jobs_now") as fetch:
            result = app_module.run_scheduled_maintenance(self.db)
        self.assertEqual(result["fetch_attempted"], False)
        fetch.assert_not_called()

    def test_no_temporal_fetch_attempted_when_never_fetched(self):
        Config.TEMPORAL_ADDRESS = ""
        with mock.patch("routes.jobs.fetch_jobs_now") as fetch:
            result = app_module.run_scheduled_maintenance(self.db)
        self.assertEqual(result["fetch_attempted"], True)
        fetch.assert_called_once_with(self.db)

    def test_auto_fetch_off_skips_fetch_when_no_temporal(self):
        Config.TEMPORAL_ADDRESS = ""
        self.db.query(Setting).filter(Setting.key == "auto_fetch").delete()
        self.db.add(Setting(key="auto_fetch", value="0"))
        self.db.commit()
        with mock.patch("routes.jobs.fetch_jobs_now") as fetch:
            result = app_module.run_scheduled_maintenance(self.db)
        self.assertEqual(result["fetch_attempted"], False)
        fetch.assert_not_called()

    def test_linkedin_ingestion_is_independent_of_temporal(self):
        # LinkedIn capture goes through ingest_job() (the API route) — it must
        # work whether or not Temporal is configured. Only the RSS/Adzuna fetch
        # is delegated.
        Config.TEMPORAL_ADDRESS = "ns.acct.tmprl.cloud:7233"
        from routes.jobs import ingest_job, JobCreate
        job = ingest_job(self.db, JobCreate(
            title="Python Developer", company="Acme Soft", location="Chennai",
            url="http://ex.com/linkedin-independent", source="linkedin",
            emails=[], phones=[], apply_link=""))
        self.assertIsNotNone(job["id"])
        self.assertEqual(len([j for j in self.db.query(Job).all()
                              if j.source == "linkedin"]), 1)


class ReadinessEndpointTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.db.query(Application).delete()
        self.db.query(Setting).delete()
        self.db.query(Job).delete()
        self.db.add(Setting(key="auto_apply", value="0"))
        self.db.add(Setting(key="auto_fetch", value="1"))
        self.db.commit()
        self.addCleanup(self.db.close)
        self._old = Config.TEMPORAL_ADDRESS
        self.addCleanup(setattr, Config, "TEMPORAL_ADDRESS", self._old)

    def test_readiness_reports_booleans_never_secrets(self):
        Config.TEMPORAL_ADDRESS = "ns.acct.tmprl.cloud:7233"
        data = app_module.readiness()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["database_configured"], True)
        self.assertEqual(data["temporal_configured"], True)
        self.assertEqual(data["job_analysis_enabled"], True)
        self.assertEqual(data["job_intelligence_enabled"], True)
        self.assertIn("groq_configured", data)
        self.assertIn("adzuna_configured", data)
        self.assertEqual(data["dashboard_auto_fetch"], True)
        # No secret-bearing keys leak anything concrete.
        joined = " ".join(str(v) for v in data.values()).lower()
        self.assertNotIn("tmprl.cloud", joined)          # no Temporal address
        self.assertNotIn("postgres", joined)             # no DB URL
        self.assertNotIn("api_key", joined)              # no key names/values

    def test_readiness_reflects_disabled_temporal(self):
        Config.TEMPORAL_ADDRESS = ""
        data = app_module.readiness()
        self.assertEqual(data["temporal_configured"], False)
        self.assertEqual(data["status"], "ok")


if __name__ == "__main__":
    unittest.main()