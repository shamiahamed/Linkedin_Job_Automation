"""Phase 4 tests — Job Intelligence Agent.

Unit-level coverage WITHOUT any network: the Groq key / LLM toggle are forced
off in the deterministic tests (only the local weighted scoring runs), and the
LLM narrative is always mocked (services.llm.intelligence_note / services.llm._chat).
Tests prove the LLM can never override the deterministic verdict, that failures
degrade safely, and that the LLM response is validated and bounded.
"""
import unittest
from unittest import mock

from config import Config
from services import job_analyzer as ja
from services import job_intelligence as ji


def _job(**kw):
    base = {"title": "", "company": "", "location": "", "description": "", "experience": ""}
    base.update(kw)
    return base


def _analysis(job):
    """Deterministic Phase-2 analysis for a job dict (LLM forced off)."""
    with mock.patch.object(Config, "GROQ_API_KEY", ""), \
            mock.patch.object(Config, "JOB_ANALYSIS_USE_LLM", "false"):
        return ja.analyze_job(job)


class DeterministicEvaluationTests(unittest.TestCase):
    """No Groq key, LLM toggle off — pure local scoring/verdict (zero network)."""

    def setUp(self):
        mock.patch.object(Config, "GROQ_API_KEY", "").start()
        mock.patch.object(Config, "JOB_INTELLIGENCE_USE_LLM", "true").start()
        self.addCleanup(mock.patch.stopall)

    def test_valid_full_match(self):
        job = _job(title="Python Developer", location="Chennai",
                   description="Python Developer, 0-2 years, FastAPI, SQL. Chennai.")
        result = ji.evaluate_job(_text(job), _analysis(job))
        self.assertEqual(result["intelligence_status"], "ok")
        self.assertEqual(result["match_status"], "matched")
        self.assertEqual(result["match_score"], 100)
        self.assertTrue(result["role_match"])
        self.assertTrue(result["experience_match"])
        self.assertTrue(result["location_match"])
        self.assertTrue(result["skill_match"])
        self.assertTrue(result["seniority_match"])
        self.assertFalse(result["walk_in"])
        self.assertEqual(result["matched_roles"], ["python developer"])
        self.assertIn("python", result["matched_skills"])
        self.assertFalse(result["llm_enriched"])

    def test_unknown_signals_force_needs_review(self):
        job = _job(title="Python Developer", location="",
                   description="Developer role at a product company.")
        result = ji.evaluate_job(_text(job), _analysis(job))
        self.assertEqual(result["match_status"], "needs_review")
        self.assertIsNone(result["experience_match"])
        self.assertIsNone(result["location_match"])
        self.assertEqual(result["match_score"], 77)  # 30 + 12 + 10 + 15 + 10 (skill hits via title)

    def test_role_mismatch_is_not_matched(self):
        job = _job(title="Accountant", location="Mumbai",
                   description="Accounts payable, 2+ years of experience.")
        result = ji.evaluate_job(_text(job), _analysis(job))
        self.assertFalse(result["role_match"])
        self.assertFalse(result["location_match"])
        self.assertEqual(result["match_status"], "not_matched")
        self.assertEqual(result["match_score"], 42)  # 0 + 25 + 0 + 7 + 10

    def test_experience_over_cap_is_not_matched(self):
        job = _job(title="Python Developer", location="Chennai",
                   description="3+ years of experience required.")
        result = ji.evaluate_job(_text(job), _analysis(job))
        self.assertFalse(result["experience_match"])
        self.assertEqual(result["match_status"], "not_matched")

    def test_walkin_signal_preserved(self):
        job = _job(title="Walk-in Interview - Python Developer", location="Chennai",
                   description="WALK-IN. Venue: Chennai. 0-1 years. Interview Date: 15-10-2026.")
        result = ji.evaluate_job(_text(job), _analysis(job))
        self.assertTrue(result["walk_in"])
        self.assertIn("Walk-in", result["reasons"][-1])

    def test_schema_shape(self):
        job = _job(title="Python Developer", location="Chennai",
                   description="Python, SQL, 0-2 years.")
        result = ji.evaluate_job(_text(job), _analysis(job))
        self.assertEqual(set(result.keys()),
                         {"intelligence_status", "evaluated_at", "llm_enriched",
                          "match_status", "match_score", "role_match", "skill_match",
                          "experience_match", "location_match", "seniority_match",
                          "walk_in", "matched_roles", "matched_skills", "missing_skills",
                          "reasons", "concerns", "summary"})

    def test_no_analysis_returns_unavailable(self):
        result = ji.evaluate_job("any text", None)
        self.assertEqual(result["intelligence_status"], "unavailable")
        self.assertEqual(result["match_status"], "unavailable")

    def test_invalid_analysis_returns_unavailable(self):
        result = ji.evaluate_job("any text", {"role": {}, "garbage": True})
        self.assertEqual(result["intelligence_status"], "unavailable")
        result = ji.evaluate_job("any text", {"analysis_status": "failed"})
        self.assertEqual(result["intelligence_status"], "unavailable")

    def test_never_raises_on_garbage(self):
        self.assertEqual(ji.evaluate_job(None, None)["intelligence_status"], "unavailable")
        self.assertEqual(ji.evaluate_job("", {},)["intelligence_status"], "unavailable")
        self.assertEqual(ji.evaluate_job("x", "not a dict")["intelligence_status"], "unavailable")


def _text(job):
    return " ".join([job.get("title") or "", job.get("location") or "",
                     job.get("experience") or "", job.get("description") or ""])


class AuthorityTests(unittest.TestCase):
    """The LLM can never override the deterministic verdict, and the narrative is
    validated. All LLM calls are mocked — no live Groq."""

    def setUp(self):
        mock.patch.object(Config, "GROQ_API_KEY", "test-key").start()
        mock.patch.object(Config, "JOB_INTELLIGENCE_USE_LLM", "true").start()
        default = mock.patch("services.llm.intelligence_note", return_value={})
        default.start()
        self.addCleanup(mock.patch.stopall)

    def _mock_note(self, ret):
        mock.patch("services.llm.intelligence_note", return_value=ret).start()

    def _raising_note(self):
        mock.patch("services.llm.intelligence_note", side_effect=RuntimeError("down")).start()

    def test_experience_mismatch_cannot_be_overridden(self):
        job = _job(title="Python Developer", location="Chennai",
                   description="Requires 5+ years of hands-on experience.")
        self._mock_note({"missing_skills": [], "summary": "Definitely a great match!!",
                         "additional_concerns": []})
        result = ji.evaluate_job(_text(job), _analysis(job))
        self.assertFalse(result["experience_match"])
        self.assertEqual(result["match_status"], "not_matched")

    def test_seniority_mismatch_cannot_be_overridden(self):
        job = _job(title="Senior Software Engineer", location="Chennai",
                   description="8+ years in distributed systems.")
        self._mock_note({"missing_skills": [], "summary": "An eager fresher can do this.",
                         "additional_concerns": []})
        result = ji.evaluate_job(_text(job), _analysis(job))
        self.assertFalse(result["seniority_match"])
        self.assertEqual(result["seniority_match"], False)
        self.assertEqual(result["match_status"], "not_matched")

    def test_location_mismatch_cannot_be_overridden(self):
        job = _job(title="Data Analyst", location="Pune",
                   description="Power BI role, 0-1 years.")
        self._mock_note({"missing_skills": [], "summary": "Remote-friendly, come anytime.",
                         "additional_concerns": []})
        result = ji.evaluate_job(_text(job), _analysis(job))
        self.assertFalse(result["location_match"])
        self.assertEqual(result["match_status"], "not_matched")

    def test_score_never_llm_rated(self):
        job = _job(title="Python Developer", location="Chennai",
                   description="Python, SQL, 0-2 years.")
        self._mock_note({"missing_skills": [], "summary": "perfect 10/10 hire",
                         "additional_concerns": []})
        result = ji.evaluate_job(_text(job), _analysis(job))
        self.assertEqual(result["match_score"], 100)  # from weights, not the LLM
        self.assertEqual(result["match_status"], "matched")

    def test_missing_api_key_uses_deterministic_only(self):
        mock.patch.object(Config, "GROQ_API_KEY", "").start()
        note_mock = mock.patch("services.llm.intelligence_note").start()
        job = _job(title="Python Developer", location="Chennai",
                   description="Python, SQL, 0-2 years.")
        result = ji.evaluate_job(_text(job), _analysis(job))
        note_mock.assert_not_called()
        self.assertFalse(result["llm_enriched"])
        self.assertEqual(result["match_status"], "matched")

    def test_llm_failure_safe(self):
        self._raising_note()
        job = _job(title="Python Developer", location="Chennai",
                   description="Python, SQL, 0-2 years.")
        result = ji.evaluate_job(_text(job), _analysis(job))
        self.assertEqual(result["intelligence_status"], "ok")
        self.assertFalse(result["llm_enriched"])
        self.assertEqual(result["match_status"], "matched")

    def test_invalid_json_safe(self):
        self._mock_note({})
        job = _job(title="Python Developer", location="Chennai",
                   description="Python, SQL, 0-2 years.")
        result = ji.evaluate_job(_text(job), _analysis(job))
        self.assertFalse(result["llm_enriched"])
        self.assertEqual(result["match_status"], "matched")

    def test_narrative_merge_is_bounded_and_validated(self):
        long_concerns = ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]
        self._mock_note({"missing_skills": ["TypeScript", " ", "TypeScript"],
                         "summary": "Bright candidate", "additional_concerns": long_concerns})
        job = _job(title="React Developer", location="Chennai",
                   description="React, 0-2 years, JavaScript.")
        result = ji.evaluate_job(_text(job), _analysis(job))
        self.assertTrue(result["llm_enriched"])
        self.assertIn("typescript", [s.lower() for s in result["missing_skills"]])
        self.assertLessEqual(len(result["concerns"]), 6)
        self.assertEqual(result["summary"], "Bright candidate")
        # narrative never touches the verdict
        self.assertTrue(result["role_match"])
        self.assertEqual(result["match_status"], "matched")

    def test_use_llm_false_keeps_deterministic_only(self):
        mock.patch.object(Config, "JOB_INTELLIGENCE_USE_LLM", "false").start()
        note_mock = mock.patch("services.llm.intelligence_note").start()
        job = _job(title="Python Developer", location="Chennai",
                   description="Python, SQL, 0-2 years.")
        result = ji.evaluate_job(_text(job), _analysis(job))
        note_mock.assert_not_called()
        self.assertFalse(result["llm_enriched"])


class LlmResponseValidationTests(unittest.TestCase):
    """services.llm.intelligence_note — parsing + bounding of a raw Groq reply."""

    def setUp(self):
        mock.patch.object(Config, "GROQ_API_KEY", "test-key").start()
        self.addCleanup(mock.patch.stopall)

    def _chat_patch(self, raw):
        return mock.patch("services.llm._chat", return_value=raw).start()

    def _note(self):
        from services import llm
        return llm.intelligence_note("Python dev job...", {
            "role_match": True, "experience_match": True, "location_match": True,
            "seniority_match": True, "skill_match": True, "walk_in": False,
        }, {"roles": ["python developer"], "skills": ["python", "sql"]})

    def test_valid_json_bounded(self):
        self._chat_patch('{"missing_skills": ["TypeScript", "Docker"], '
                         '"summary": "Good fit", "additional_concerns": ["Needs TS"]}')
        note = self._note()
        self.assertEqual(note["missing_skills"], ["TypeScript", "Docker"])
        self.assertEqual(note["summary"], "Good fit")
        self.assertEqual(note["additional_concerns"], ["Needs TS"])

    def test_garbage_types_validated(self):
        self._chat_patch('{"missing_skills": "typescript, sql", "summary": 12345, '
                         '"additional_concerns": [1, 2, 3]}')
        note = self._note()
        self.assertEqual(note["missing_skills"], ["typescript", "sql"])  # comma-string split
        self.assertEqual(note["summary"], "12345")  # coerced to string
        self.assertEqual(note["additional_concerns"], ["1", "2", "3"])

    def test_too_long_values_truncated(self):
        self._chat_patch('{"missing_skills": ["' + "x" * 500 + '"], "summary": "' + "y" * 500 + '"}')
        note = self._note()
        self.assertTrue(all(len(s) <= 80 for s in note["missing_skills"]))
        self.assertLessEqual(len(note["summary"]), 400)

    def test_invalid_json_returns_empty(self):
        self._chat_patch("this is not json at all")
        self.assertEqual(self._note(), {})

    def test_timeout_returns_empty(self):
        self._chat_patch(None)
        self.assertEqual(self._note(), {})

    def test_llm_raise_returns_empty(self):
        mock.patch("services.llm._chat", side_effect=RuntimeError("groq down")).start()
        self.assertEqual(self._note(), {})

    def test_missing_key_returns_empty(self):
        mock.patch.object(Config, "GROQ_API_KEY", "").start()
        self._chat_patch('{"summary": "nope"}')
        self.assertEqual(self._note(), {})


if __name__ == "__main__":
    unittest.main()