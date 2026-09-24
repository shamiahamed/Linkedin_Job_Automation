"""Phase 4 tests — Job Intelligence in the real ingest pipeline.

App-level: a throwaway SQLite DB (Config.DATABASE_URL switched BEFORE `main`/
`database` are imported) so create_all + the SQLite PRAGMA migration actually
run against the `job_intelligence` column. Uses the shared ingest_job() capture
pipeline; auto-apply stays OFF via the settings table so no email/phone side
effects. All Config mutations and LLM mocks are restored afterwards.
"""
import os
import tempfile
import unittest
from unittest import mock

from config import Config

# Switch app settings BEFORE importing main/database.
_tmp = tempfile.mkdtemp(prefix="jobintel_")
Config.DATABASE_URL = "sqlite:///" + os.path.join(_tmp, "test.db").replace(os.sep, "/")
Config.API_TOKEN = ""
Config.APP_PASSWORD = ""
Config.GROQ_API_KEY = ""
Config.JOB_ANALYSIS_ENABLED = "true"
Config.JOB_ANALYSIS_USE_LLM = "false"
Config.JOB_INTELLIGENCE_ENABLED = "true"
Config.JOB_INTELLIGENCE_USE_LLM = "false"

from database import SessionLocal  # noqa: E402
from models import Job, Application, Setting  # noqa: E402
from routes.jobs import ingest_job, JobCreate  # noqa: E402
import main as app_module  # noqa: E402  (runs create_all + SQLite PRAGMA migration)


def _job_data(**kw):
    base = {
        "title": "Python Developer",
        "company": "Acme Soft",
        "location": "Chennai",
        "url": "http://example.com/intel-job",
        "description": "Python Developer, 1-2 years, FastAPI, SQL. Chennai office.",
        "emails": [],
        "phones": [],
        "source": "auto_fetch",
        "apply_link": "http://example.com/job/apply",
    }
    base.update(kw)
    return JobCreate(**base)


class IngestIntelligenceTests(unittest.TestCase):
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

    def _reload(self, job_id):
        self.db.expire_all()
        return self.db.query(Job).filter(Job.id == job_id).first()

    def test_ingest_stores_intelligence_without_side_effects(self):
        job = ingest_job(self.db, _job_data())
        row = self._reload(job["id"])
        self.assertIsNotNone(row.job_intelligence)
        self.assertEqual(row.job_intelligence["intelligence_status"], "ok")
        self.assertEqual(row.job_intelligence["match_status"], "matched")
        self.assertIn("job_intelligence", job)
        # existing behaviour unchanged: no contacts -> apply_link card, no email.
        self.assertEqual(row.status, "apply_link")
        self.assertFalse(row.has_email)
        self.assertEqual(row.job_analysis["analysis_status"], "ok")

    def test_intelligence_disabled_stores_none(self):
        mock.patch.object(Config, "JOB_INTELLIGENCE_ENABLED", "false").start()
        self.addCleanup(mock.patch.stopall)
        job = ingest_job(self.db, _job_data(url="http://example.com/intel-off"))
        row = self._reload(job["id"])
        self.assertIsNone(row.job_intelligence)
        self.assertIsNotNone(row.job_analysis)  # Phase 2 unaffected

    def test_intelligence_failure_still_inserts_job(self):
        mock.patch(
            "services.job_intelligence.evaluate_job",
            side_effect=RuntimeError("intel boom"),
        ).start()
        self.addCleanup(mock.patch.stopall)
        job = ingest_job(self.db, _job_data(url="http://example.com/intel-fail"))
        row = self._reload(job["id"])
        self.assertIsNotNone(row)
        self.assertEqual(row.title, "Python Developer")
        self.assertEqual(row.job_intelligence["intelligence_status"], "unavailable")

    def test_intelligence_without_analysis_is_unavailable(self):
        mock.patch.object(Config, "JOB_ANALYSIS_ENABLED", "false").start()
        self.addCleanup(mock.patch.stopall)
        job = ingest_job(self.db, _job_data(url="http://example.com/intel-noana"))
        row = self._reload(job["id"])
        self.assertIsNotNone(row)  # job created anyway
        self.assertEqual(row.job_intelligence["match_status"], "unavailable")

    def test_llm_narrative_merge_in_ingest(self):
        mock.patch.object(Config, "GROQ_API_KEY", "test-key").start()
        mock.patch.object(Config, "JOB_INTELLIGENCE_USE_LLM", "true").start()
        mock.patch(
            "services.llm.intelligence_note",
            return_value={"missing_skills": ["Redis"], "summary": "On-site Chennai role.",
                          "additional_concerns": []},
        ).start()
        self.addCleanup(mock.patch.stopall)
        job = ingest_job(self.db, _job_data(url="http://example.com/intel-llm"))
        row = self._reload(job["id"])
        self.assertTrue(row.job_intelligence["llm_enriched"])
        self.assertEqual(row.job_intelligence["summary"], "On-site Chennai role.")
        self.assertIn("Redis", row.job_intelligence["missing_skills"])


if __name__ == "__main__":
    unittest.main()