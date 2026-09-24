"""Phase 2 tests — Job Analysis Agent (deterministic core).

Unit-level coverage of the analyzer WITHOUT any network: the Groq key and the
LLM toggle are forced off in DeterministicTests so only the deterministic core
runs; LlmMergeTests mock services.llm.analyze_job to prove the LLM can never
override the core and that failures degrade safely.
"""
import unittest
from unittest import mock

from config import Config
from services import job_analyzer as ja


def _job(**kw):
    base = {"title": "", "company": "", "location": "", "description": "", "experience": ""}
    base.update(kw)
    return base


class DeterministicTests(unittest.TestCase):
    """Core-only runs: no Groq key, LLM toggle off — zero network."""

    def setUp(self):
        mock.patch.object(Config, "GROQ_API_KEY", "").start()
        mock.patch.object(Config, "JOB_ANALYSIS_USE_LLM", "false").start()
        mock.patch.object(Config, "JOB_ANALYSIS_ENABLED", "true").start()
        self.addCleanup(mock.patch.stopall)

    def test_react_1_2_years_match(self):
        a = ja.analyze_job(_job(
            title="React Developer",
            location="Chennai",
            description="Hiring a React Developer with 1-2 years of experience. "
                        "Skills: React, Redux, JavaScript.",
        ))
        self.assertEqual(a["analysis_status"], "ok")
        self.assertTrue(a["role"]["matched"])
        self.assertEqual(a["role"]["matched_role"], "react developer")
        self.assertTrue(a["experience"]["matched"])
        self.assertEqual(a["experience"]["min_years"], 1)
        self.assertTrue(a["location"]["matched"])
        self.assertTrue(a["overall_match"]["is_match"])

    def test_three_plus_years_no_match(self):
        a = ja.analyze_job(_job(
            title="Python Developer",
            location="Pune",
            description="3+ years of experience required.",
        ))
        self.assertFalse(a["experience"]["matched"])
        self.assertFalse(a["location"]["matched"])
        self.assertFalse(a["overall_match"]["is_match"])

    def test_fresher_match(self):
        a = ja.analyze_job(_job(
            title="Fresher Software Engineer",
            location="Madurai",
            description="Freshers welcome. 0-1 years of experience. Python basics.",
        ))
        self.assertTrue(a["experience"]["matched"])
        self.assertEqual(a["seniority"]["level"], "entry")
        self.assertTrue(a["overall_match"]["is_match"])

    def test_python_0_to_2_match(self):
        a = ja.analyze_job(_job(
            title="Python Developer",
            location="Chennai",
            description="Python Developer, 0-2 years experience, FastAPI, "
                        "PostgreSQL. On-site at Chennai.",
        ))
        self.assertTrue(a["role"]["matched"])
        self.assertTrue(a["experience"]["matched"])
        self.assertTrue(a["skills"]["matched"])
        self.assertIn("python", a["skills"]["matched_skills"])
        self.assertTrue(a["overall_match"]["is_match"])

    def test_senior_5_plus_no_match(self):
        a = ja.analyze_job(_job(
            title="Senior Software Engineer",
            location="Bengaluru",
            description="8+ years of experience in distributed systems required.",
        ))
        self.assertFalse(a["experience"]["matched"])
        self.assertEqual(a["seniority"]["level"], "senior")
        self.assertFalse(a["overall_match"]["is_match"])

    def test_missing_experience_is_unknown(self):
        a = ja.analyze_job(_job(
            title="React Developer",
            location="Chennai",
            description="We need a React Developer. Apply today.",
        ))
        self.assertIsNone(a["experience"]["matched"])
        self.assertIsNone(a["overall_match"]["is_match"])

    def test_locations_match(self):
        for loc in ("Chennai", "Madurai"):
            a = ja.analyze_job(_job(
                title="Python Developer",
                location=loc,
                description="0-1 years experience. Python and SQL.",
            ))
            self.assertTrue(a["location"]["matched"], loc)
        remote = ja.analyze_job(_job(
            title="Python Developer",
            location="Remote",
            description="Fully remote role. Python, FastAPI, 0-2 years.",
        ))
        self.assertTrue(remote["location"]["matched"])
        self.assertEqual(remote["location"]["type"], "remote")
        wfh = ja.analyze_job(_job(
            title="Python Developer",
            location="",
            description="Work from home. Python backend, 1-2 years.",
        ))
        self.assertTrue(wfh["location"]["matched"])

    def test_outside_location_mismatch(self):
        a = ja.analyze_job(_job(
            title="Data Analyst",
            location="Mumbai",
            description="Data analyst role, 3+ years. Power BI.",
        ))
        self.assertFalse(a["location"]["matched"])
        self.assertFalse(a["overall_match"]["is_match"])

    def test_unrelated_role_no_match(self):
        a = ja.analyze_job(_job(
            title="Accountant",
            location="Chennai",
            description="Accounts payable, 2+ years of experience.",
        ))
        self.assertFalse(a["role"]["matched"])
        self.assertFalse(a["overall_match"]["is_match"])

    def test_walkin_detection_with_date_time_venue(self):
        a = ja.analyze_job(_job(
            title="Walk-in Interview - Python Developer",
            location="Chennai",
            description=(
                "WALK-IN INTERVIEW at TCS.\n"
                "Venue: TCS Siruseri, Chennai\n"
                "Interview Date: 15-10-2026\n"
                "Time: 9:30 AM - 4:30 PM\n"
                "Role: Python Developer, 0-1 years of experience.\n"
            ),
        ))
        self.assertTrue(a["walk_in"]["is_walk_in"])
        self.assertEqual(a["walk_in"]["date"], "2026-10-15")
        self.assertIn("9:30 AM", a["walk_in"]["time"])
        self.assertIn("Siruseri", a["walk_in"]["venue"])
        self.assertTrue(a["overall_match"]["is_match"])

    def test_walkin_negative(self):
        a = ja.analyze_job(_job(
            title="Python Developer",
            location="Chennai",
            description="Regular posting, apply via the link.",
        ))
        self.assertFalse(a["walk_in"]["is_walk_in"])

    def test_missing_description_safe(self):
        a = ja.analyze_job(_job(title="React Developer", description=None))
        self.assertEqual(a["analysis_status"], "ok")
        self.assertTrue(a["role"]["matched"])
        self.assertIsNone(a["experience"]["matched"])
        self.assertIsNone(a["overall_match"]["is_match"])

    def test_skill_never_vetoes_role(self):
        a = ja.analyze_job(_job(
            title="React Developer",
            location="Chennai",
            description="1-2 years. React work at a product startup.",
        ))
        self.assertTrue(a["role"]["matched"])
        self.assertIsNone(a["skills"]["matched"])  # no profile skill named
        self.assertTrue(a["overall_match"]["is_match"])

    def test_never_raises_on_garbage(self):
        self.assertEqual(ja.analyze_job(_job()).get("analysis_status"), "ok")
        self.assertEqual(ja.analyze_job(_job(title="!", description="???")).get("analysis_status"), "ok")
        self.assertEqual(ja.analyze_job(None)["analysis_status"], "ok")
        self.assertEqual(ja.analyze_job({"title": "#@!", "description": "\x00"})["analysis_status"], "ok")

    def test_schema_shape(self):
        a = ja.analyze_job(_job(
            title="Python Developer",
            location="Chennai",
            description="Python, SQL, 0-1 years, Chennai.",
        ))
        self.assertEqual(set(a.keys()),
                         {"analysis_status", "analyzed_at", "llm_enriched", "role",
                          "experience", "skills", "location", "seniority", "walk_in",
                          "overall_match"})
        for section in ("role", "experience", "skills", "location", "seniority"):
            self.assertIn("matched", a[section])
        self.assertEqual(set(a["walk_in"]),
                         {"is_walk_in", "date", "time", "venue"})
        self.assertEqual(set(a["overall_match"]),
                         {"is_match", "confidence", "summary"})

    def test_experience_label_passthrough(self):
        a = ja.analyze_job(_job(
            title="Python Developer",
            location="Chennai",
            experience="Fresher",
            description="Role at Chennai office.",
        ))
        self.assertTrue(a["experience"]["matched"])
        self.assertEqual(a["experience"]["min_years"], 0)


class LlmMergeTests(unittest.TestCase):
    """LLM enrichment — mocked, never hits Groq. Core authority preserved."""

    def setUp(self):
        mock.patch.object(Config, "GROQ_API_KEY", "test-key").start()
        mock.patch.object(Config, "JOB_ANALYSIS_USE_LLM", "true").start()
        mock.patch.object(Config, "JOB_ANALYSIS_ENABLED", "true").start()
        llm_patch = mock.patch("services.llm.analyze_job", return_value={})
        llm_patch.start()
        self.addCleanup(mock.patch.stopall)

    def _mock_llm(self, ret):
        mock.patch("services.llm.analyze_job", return_value=ret).start()

    def test_invalid_llm_json_unchanged(self):
        self._mock_llm({})
        a = ja.analyze_job(_job(
            title="Python Developer",
            location="Chennai",
            description="Python, SQL, 0-2 years.",
        ))
        self.assertFalse(a["llm_enriched"])
        self.assertTrue(a["overall_match"]["is_match"])

    def test_llm_timeout_safe(self):
        self._mock_llm(None)  # simulate timeout product -> None
        a = ja.analyze_job(_job(title="Python Developer", description="Anything"))
        self.assertEqual(a["analysis_status"], "ok")
        self.assertFalse(a["llm_enriched"])

    def test_llm_raise_safe(self):
        mock.patch(
            "services.llm.analyze_job",
            side_effect=RuntimeError("groq down"),
        ).start()
        a = ja.analyze_job(_job(title="Python Developer", description="Anything"))
        self.assertEqual(a["analysis_status"], "ok")
        self.assertFalse(a["llm_enriched"])

    def test_llm_cannot_override_core_decision(self):
        self._mock_llm({
            "role_matched": False,
            "location_matched": False,
            "experience_min_years": 10,
            "skills_matched": [],
            "summary": "definitely not a match",
        })
        a = ja.analyze_job(_job(
            title="Python Developer",
            location="Chennai",
            description="Python, SQL, 0-2 years of experience in Chennai.",
        ))
        # Core decided everything -> LLM's counter-verdict is ignored.
        self.assertTrue(a["role"]["matched"])
        self.assertTrue(a["location"]["matched"])
        self.assertTrue(a["experience"]["matched"])
        self.assertTrue(a["overall_match"]["is_match"])

    def test_llm_fills_undecided_fields_only(self):
        self._mock_llm({
            "role_matched": None,
            "experience_min_years": 0,
            "experience_max_years": 2,
            "location_matched": True,
            "location_type": "on-site",
            "skills_matched": ["python", "sql"],
            "seniority_level": "unknown",
            "walk_in": {"is_walk_in": False, "date": None, "time": None, "venue": None},
            "summary": "Looks like a Chennai office role.",
        })
        a = ja.analyze_job(_job(
            title="Python Developer",
            description="Developer role at a product company.",
            location="",
        ))
        self.assertTrue(a["llm_enriched"])
        self.assertTrue(a["location"]["matched"])  # was None -> filled by LLM
        self.assertTrue(a["experience"]["matched"])  # was None -> filled by LLM
        self.assertTrue(a["overall_match"]["is_match"])  # recomputed to a match


if __name__ == "__main__":
    unittest.main()