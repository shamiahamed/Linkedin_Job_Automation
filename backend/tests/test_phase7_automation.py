"""Phase 7 tests — Automation/Workflow observability endpoint + run ledger.

Covers: readiness still works, /api/automation/status shape, disabled agents
report disabled, missing Temporal reports not-configured, workflow runs get
recorded and aggregated, the recent-activity feed uses only real data, and no
secret ever reaches the serialized response (keys, addresses, tokens, URLs).
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from config import Config

# Switch app settings BEFORE importing main/database. IMPORTANT: like every test
# module here, Config is process-global at import time — the LAST module to
# import wins the runtime flags. So we must NOT flip the *ENABLED flags in this
# header (the Phase 6 modules rely on them staying "true"); each test that needs
# a specific value patches Config inside the test instead.
_tmp = tempfile.mkdtemp(prefix="jobauto7_")
Config.DATABASE_URL = "sqlite:///" + os.path.join(_tmp, "test.db").replace(os.sep, "/")
Config.API_TOKEN = ""
Config.APP_PASSWORD = ""
Config.GROQ_API_KEY = ""
Config.TEMPORAL_ADDRESS = ""
Config.TEMPORAL_API_KEY = ""
Config.ADZUNA_APP_ID = ""
Config.ADZUNA_APP_KEY = ""

from database import SessionLocal  # noqa: E402
from models import Job, Application, Setting, AutomationRun  # noqa: E402
from routes.jobs import fetch_jobs_now, ingest_job, JobCreate  # noqa: E402
from routes.automation import automation_status, readiness_body  # noqa: E402
import main as app_module  # noqa: E402


def _fake_item(url: str, title: str = "Python Developer") -> dict:
    return {
        "title": title, "company": "Acme Soft", "location": "Chennai",
        "url": url, "description": "FastAPI and PostgreSQL service work.",
        "emails": [], "phones": [], "experience": "0-1 years", "salary": "",
        "source": "auto_fetch", "apply_link": "",
    }


class AutomationStatusTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.db.query(AutomationRun).delete()
        self.db.query(Application).delete()
        self.db.query(Setting).delete()
        self.db.query(Job).delete()
        self.db.add(Setting(key="auto_apply", value="0"))
        self.db.add(Setting(key="auto_fetch", value="0"))
        self.db.commit()
        self.addCleanup(self.db.close)

    # 1) Readiness endpoint still works (Phase 6 compatibility).
    def test_readiness_endpoint_still_works(self):
        data = app_module.readiness()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["database_configured"])
        for key in ("app", "temporal_configured", "job_analysis_enabled",
                    "job_intelligence_enabled", "groq_configured",
                    "adzuna_configured", "dashboard_auto_fetch"):
            self.assertIn(key, data)
        # Delegates to the shared shape — the Automation view and /api/readiness
        # can never drift.
        self.assertEqual(data, readiness_body())

    # 2) Automation status endpoint structure.
    def test_automation_status_structure(self):
        data = automation_status(self.db)
        self.assertEqual(set(["readiness", "sources", "agents", "workflows",
                              "recent_activity"]), set(data.keys()))
        self.assertIn("linkedin", data["sources"])
        self.assertIn("google_rss", data["sources"])
        self.assertIn("adzuna", data["sources"])
        self.assertIn("job_analysis", data["agents"])
        self.assertIn("job_intelligence", data["agents"])
        self.assertIn("external_job_fetch", data["workflows"])

    def test_linkedin_source_is_available_and_counts_captures(self):
        ingest_job(self.db, JobCreate(
            title="Data Analyst", company="Data Minds", location="Bengaluru",
            url="http://ex.com/p7-linkedin", source="linkedin",
            emails=[], phones=[], apply_link=""))
        data = automation_status(self.db)
        li = data["sources"]["linkedin"]
        self.assertEqual(li["type"], "Event-driven")
        self.assertEqual(li["status"], "Available")
        self.assertEqual(li["captures_total"], 1)
        self.assertIsNotNone(li["last_capture_at"])
        # No "Running" claim without a live heartbeat.
        self.assertNotEqual(li["status"], "Running")

    # 5) Disabled agents report disabled; LLM flags are separate/safe.
    def test_disabled_agents_report_disabled(self):
        with mock.patch.object(Config, "JOB_ANALYSIS_ENABLED", "false"), \
             mock.patch.object(Config, "JOB_INTELLIGENCE_ENABLED", "false"):
            data = automation_status(self.db)
        self.assertFalse(data["readiness"]["job_analysis_enabled"])
        self.assertFalse(data["readiness"]["job_intelligence_enabled"])
        self.assertFalse(data["agents"]["job_analysis"]["enabled"])
        self.assertFalse(data["agents"]["job_intelligence"]["enabled"])
        # Booleans only — no keys, no URLs.
        self.assertIsInstance(data["agents"]["job_analysis"]["llm_enabled"], bool)

    # 6) Missing Temporal configuration reports not-configured.
    def test_missing_temporal_reports_not_configured(self):
        def _t(): return Config.TEMPORAL_ADDRESS
        self.addCleanup(setattr, Config, "TEMPORAL_ADDRESS", _t())
        Config.TEMPORAL_ADDRESS = ""
        data = automation_status(self.db)
        self.assertFalse(data["readiness"]["temporal_configured"])
        wf = data["workflows"]["external_job_fetch"]
        self.assertFalse(wf["handled_by_temporal"])
        self.assertIn("built-in scheduler", wf["frequency"].lower())

    # 7) Workflow runs recorded (restores the real fetch pipeline, mocked net).
    def test_workflow_runs_recorded_and_aggregated(self):
        old_id, old_key = Config.ADZUNA_APP_ID, Config.ADZUNA_APP_KEY
        self.addCleanup(setattr, Config, "ADZUNA_APP_ID", old_id)
        self.addCleanup(setattr, Config, "ADZUNA_APP_KEY", old_key)
        Config.ADZUNA_APP_ID = "test-app-id"
        Config.ADZUNA_APP_KEY = "test-app-key"
        adzuna_items = [_fake_item("http://ex.com/p7-a1", "Role A"),
                        _fake_item("http://ex.com/p7-a2", "Role B")]
        rss_items = [_fake_item("http://ex.com/p7-r1", "Role C")]
        with mock.patch("services.job_search.fetch_daily_jobs",
                        return_value=adzuna_items), \
             mock.patch("services.rss_source.fetch_google_rss_jobs",
                        return_value=rss_items):
            result = fetch_jobs_now(self.db)
        self.assertTrue(result["success"])
        self.assertEqual(result["fetched"], 3)
        self.assertEqual(result["added"], 3)
        rows = self.db.query(AutomationRun).order_by(AutomationRun.id).all()
        self.assertEqual(len(rows), 2)
        by_source = {r.source: r for r in rows}
        self.assertEqual(by_source["adzuna"].items_found, 2)
        self.assertEqual(by_source["adzuna"].items_ingested, 2)
        self.assertEqual(by_source["google_rss"].items_found, 1)
        self.assertEqual(by_source["google_rss"].items_ingested, 1)
        self.assertEqual(by_source["adzuna"].workflow, "external_job_fetch")

        data = automation_status(self.db)
        wf = data["workflows"]["external_job_fetch"]
        self.assertEqual(wf["status"], "completed")
        self.assertEqual(wf["runs_total"], 1)
        self.assertEqual(wf["last_found"], 3)
        self.assertEqual(wf["last_ingested"], 3)
        self.assertIsNotNone(wf["last_run"])
        self.assertIsNone(wf["next_run"])  # never invented

    def test_activity_feed_only_uses_real_data(self):
        old_id, old_key = Config.ADZUNA_APP_ID, Config.ADZUNA_APP_KEY
        self.addCleanup(setattr, Config, "ADZUNA_APP_ID", old_id)
        self.addCleanup(setattr, Config, "ADZUNA_APP_KEY", old_key)
        Config.ADZUNA_APP_ID = "test-app-id"
        Config.ADZUNA_APP_KEY = "test-app-key"
        with mock.patch("services.job_search.fetch_daily_jobs",
                        return_value=[_fake_item("http://ex.com/p7-f1")]), \
             mock.patch("services.rss_source.fetch_google_rss_jobs",
                        return_value=[]):
            fetch_jobs_now(self.db)
        ingest_job(self.db, JobCreate(
            title="Support Engineer", company="HelpCo", location="Kochi",
            url="http://ex.com/p7-capture", source="linkedin",
            emails=[], phones=[], apply_link=""))
        data = automation_status(self.db)
        feed = data["recent_activity"]
        self.assertTrue(feed, "feed must not be empty with real activity")
        texts = " ".join(i["text"] for i in feed)
        self.assertIn("External job fetch completed", texts)
        self.assertIn("Job captured", texts)
        self.assertIn("Support Engineer", texts)
        for item in feed:
            self.assertIn(item["level"], ("ok", "warn", "info"))
            self.assertTrue(item["text"])
        self.assertLessEqual(len(feed), 12)

    # 3) Secrets / credentials are NEVER serialized.
    def test_secrets_never_serialized(self):
        fake = {
            "TEMPORAL_ADDRESS": "ns-secret.acme.tmprl.cloud:7233",
            "TEMPORAL_API_KEY": "TEMPORALCLOUDKEY-super-secret-777",
            "TEMPORAL_NAMESPACE": "acme-prod",
            "GROQ_API_KEY": "gsk_secret_groq_42",
            "ADZUNA_APP_ID": "adzuna-app-id-1",
            "ADZUNA_APP_KEY": "adzuna-app-key-2",
            "GMAIL_REFRESH_TOKEN": "4/AGUMGmailRefreshTokenSecret123",
            "GMAIL_CLIENT_SECRET": "gmail-client-secret-abc",
            "SESSION_SECRET": "session-secret-value",
            "DATABASE_URL": "postgresql://user:hunter2@db.example.com/proddb",
        }
        old = {k: getattr(Config, k) for k in fake}
        for k, v in fake.items():
            setattr(Config, k, v)
        self.addCleanup(lambda: [setattr(Config, k, v) for k, v in old.items()])

        payload = automation_status(self.db)
        text = json.dumps(payload)
        for field, secret in fake.items():
            self.assertNotIn(secret, text, f"{field} value leaked into payload")
        # Even the field names for credential material must not appear.
        for marker in ("api_key", "client_secret", "refresh_token",
                       "hunter2", "db.example.com"):
            self.assertNotIn(marker, text)
        # The readiness slice inside the payload must be equally clean.
        self.assertNotIn(fake["TEMPORAL_ADDRESS"], json.dumps(payload["readiness"]))

    # 4) Temporal cluster address is never serialized even when configured.
    def test_temporal_address_never_serialized_when_configured(self):
        old = Config.TEMPORAL_ADDRESS
        self.addCleanup(setattr, Config, "TEMPORAL_ADDRESS", old)
        Config.TEMPORAL_ADDRESS = "prod-ns.big9.mydomain.tmprl.cloud:7233"
        text = json.dumps(automation_status(self.db))
        self.assertNotIn("prod-ns.big9.mydomain.tmprl.cloud", text)
        self.assertIn("temporal_configured", text)  # boolean, not the address

    # Existing dashboard API behaviour is unchanged.
    def test_existing_jobs_and_stats_api_unchanged(self):
        from routes.jobs import list_jobs, get_stats

        ingest_job(self.db, JobCreate(
            title="Backend Dev", company="Cloud Works", location="Chennai",
            url="http://ex.com/p7-existing", source="linkedin",
            emails=[], phones=[], apply_link=""))
        page = list_jobs(page=1, per_page=10, db=self.db)
        self.assertIn("items", page)
        self.assertIn("total", page)
        self.assertEqual(page["total"], 1)
        stats = get_stats(self.db)
        for key in ("total_jobs", "captured", "ready_to_send", "applied",
                    "pending", "auto_fetch", "saved"):
            self.assertIn(key, stats)

    def test_failed_fetch_records_failed_run_briefly(self):
        old_id, old_key = Config.ADZUNA_APP_ID, Config.ADZUNA_APP_KEY
        self.addCleanup(setattr, Config, "ADZUNA_APP_ID", old_id)
        self.addCleanup(setattr, Config, "ADZUNA_APP_KEY", old_key)
        Config.ADZUNA_APP_ID = ""
        Config.ADZUNA_APP_KEY = ""
        result = fetch_jobs_now(self.db)
        self.assertFalse(result["success"])
        row = self.db.query(AutomationRun).filter(
            AutomationRun.source == "adzuna").order_by(AutomationRun.id.desc()).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.status, "failed")
        self.assertLessEqual(len(row.error_summary or ""), 255)


if __name__ == "__main__":
    unittest.main()