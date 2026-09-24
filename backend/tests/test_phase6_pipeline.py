"""Phase 6 — full ingestion matrix + analysis/intelligence failure isolation.

All three sources (LinkedIn, Google RSS, Adzuna) must flow through the SAME
ingest_job() pipeline: dedupe -> deterministic Job Analysis -> Job Intelligence
-> Job insert -> existing downstream automation. There is deliberately NO
source-specific intelligence logic — every source lands a job with identical
enrichment. Any disabled/failed/LLM-unavailable state still ingests a valid job.
"""
import os
import tempfile
import unittest
from unittest import mock

from config import Config

# Switch app settings BEFORE importing main/database.
_tmp = tempfile.mkdtemp(prefix="jobp6_")
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
import main as app_module  # noqa: E402


def _job(**kw):
    base = {
        "title": "Python Developer",
        "company": "Cloud Works",
        "location": "Bengaluru",
        "url": "http://ex.com/base-role",
        "description": "Build and ship FastAPI services.",
        "emails": ["jobs@cloudworks.in"],
        "phones": [],
        "experience": "0-1 years",
        "salary": "",
        "source": "linkedin",
        "apply_link": "http://ex.com/apply",
    }
    base.update(kw)
    return JobCreate(**base)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.db.query(Application).delete()
        self.db.query(Setting).delete()
        self.db.query(Job).delete()
        self.db.add(Setting(key="auto_apply", value="0"))
        self.db.commit()
        self.addCleanup(self.db.close)

    def _reload(self, job_id):
        self.db.expire_all()
        return self.db.query(Job).filter(Job.id == job_id).first()

    # ---- Source matrix -----------------------------------------------------
    def test_all_three_sources_share_one_pipeline(self):
        picks = [("linkedin", "Python Developer", "Cloud Works", "http://ex.com/src-linkedin"),
                 ("auto_fetch", "Data Analyst", "Data Minds", "http://ex.com/src-rss"),
                 ("auto_fetch", "DevOps Engineer", "Infra Labs", "http://ex.com/src-adzuna")]
        for source, title, company, url in picks:
            job = ingest_job(self.db, _job(source=source, url=url, title=title,
                                           company=company, description="role " + url))
            row = self._reload(job["id"])
            self.assertEqual(row.source, source)
            self.assertEqual(row.job_analysis["analysis_status"], "ok")
            self.assertEqual(row.job_intelligence["intelligence_status"], "ok")
            self.assertIn("match_status", row.job_intelligence)
        self.assertEqual(self.db.query(Job).count(), 3)

    # ---- Pipeline order: dedupe BEFORE analysis ----------------------------
    def test_dedupe_happens_before_analysis(self):
        valid = {
            "analysis_status": "ok", "analyzed_at": "2026-01-01T00:00:00Z",
            "llm_enriched": False,
            "role": {"matched": True, "matched_role": "python developer", "reason": ""},
            "experience": {"matched": True, "min_years": 0, "max_years": 1,
                           "required_text": "", "reason": ""},
            "skills": {"matched": True, "matched_skills": ["FastAPI"],
                       "missing_skills": [], "reason": ""},
            "location": {"matched": True, "type": "city", "reason": ""},
            "seniority": {"matched": True, "level": "mid", "reason": ""},
            "walk_in": {"is_walk_in": False, "date": "", "time": "", "venue": ""},
            "overall_match": {"is_match": True, "confidence": "high",
                              "summary": "All key signals match."},
        }
        with mock.patch.object(Config, "JOB_INTELLIGENCE_ENABLED", "false"), \
             mock.patch("services.job_analyzer.analyze_job") as m:
            m.return_value = valid
            first = ingest_job(self.db, _job(url="http://ex.com/dedup-order", emails=[]))
            self.assertEqual(m.call_count, 1)
            second = ingest_job(self.db, _job(url="http://ex.com/dedup-order", emails=[]))
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(m.call_count, 1)  # duplicate never re-analyzed
        self.assertEqual(self.db.query(Job).count(), 1)

    def test_content_dedupe_without_url(self):
        first = ingest_job(self.db, _job(url=None, source="auto_fetch"))
        second = ingest_job(self.db, _job(url=None, source="auto_fetch"))
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.db.query(Job).count(), 1)

    def test_different_urls_are_distinct_jobs(self):
        a = ingest_job(self.db, _job(url="http://ex.com/one", title="Python Developer",
                                     company="Cloud Works"))
        b = ingest_job(self.db, _job(url="http://ex.com/two", title="DevOps Engineer",
                                     company="Infra Labs"))
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(self.db.query(Job).count(), 2)

    # ---- Failure isolation -------------------------------------------------
    def test_analysis_disabled_job_still_ingests(self):
        with mock.patch.object(Config, "JOB_ANALYSIS_ENABLED", "false"), \
             mock.patch.object(Config, "JOB_INTELLIGENCE_ENABLED", "false"):
            job = ingest_job(self.db, _job(url="http://ex.com/ana-off"))
        row = self._reload(job["id"])
        self.assertIsNotNone(row)
        self.assertIsNone(row.job_analysis)
        self.assertIsNone(row.job_intelligence)

    def test_intelligence_disabled_analysis_still_stored(self):
        with mock.patch.object(Config, "JOB_INTELLIGENCE_ENABLED", "false"):
            job = ingest_job(self.db, _job(url="http://ex.com/intel-off"))
        row = self._reload(job["id"])
        self.assertIsNotNone(row.job_analysis)
        self.assertEqual(row.job_analysis["analysis_status"], "ok")
        self.assertIsNone(row.job_intelligence)

    def test_analysis_failure_isolation(self):
        with mock.patch("services.job_analyzer.analyze_job",
                        side_effect=RuntimeError("analyzer down")):
            job = ingest_job(self.db, _job(url="http://ex.com/ana-fail"))
        row = self._reload(job["id"])
        self.assertIsNotNone(row)
        self.assertEqual(row.title, "Python Developer")
        self.assertEqual(row.job_analysis["analysis_status"], "failed")
        # No deterministic analysis to judge against -> intelligence is unavailable.
        self.assertEqual(row.job_intelligence["intelligence_status"], "unavailable")

    def test_intelligence_failure_isolation(self):
        with mock.patch("services.job_intelligence.evaluate_job",
                        side_effect=RuntimeError("intel down")):
            job = ingest_job(self.db, _job(url="http://ex.com/intel-fail"))
        row = self._reload(job["id"])
        self.assertIsNotNone(row)
        self.assertEqual(row.job_analysis["analysis_status"], "ok")
        self.assertEqual(row.job_intelligence["intelligence_status"], "unavailable")

    def test_llm_unreachable_falls_back_to_deterministic(self):
        with mock.patch.object(Config, "GROQ_API_KEY", "test-key"), \
             mock.patch.object(Config, "JOB_INTELLIGENCE_USE_LLM", "true"), \
             mock.patch("services.llm.intelligence_note",
                        side_effect=RuntimeError("llm down")):
            job = ingest_job(self.db, _job(url="http://ex.com/llm-down"))
        row = self._reload(job["id"])
        self.assertIsNotNone(row)
        self.assertEqual(row.job_intelligence["intelligence_status"], "ok")
        self.assertFalse(row.job_intelligence["llm_enriched"])

    def test_duplicate_ingest_creates_no_second_row(self):
        first = ingest_job(self.db, _job(url="http://ex.com/dup-url"))
        second = ingest_job(self.db, _job(url="http://ex.com/dup-url"))
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.db.query(Job).count(), 1)


if __name__ == "__main__":
    unittest.main()