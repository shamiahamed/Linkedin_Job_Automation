"""Phase 6 — auto-apply safety + notification safety.

Job Intelligence must NEVER override existing safety gates (experience gate,
email/phone requirements, auto-apply setting, confirm-before-send, duplicate
prevention, status rules). The intelligence score is display-only and must not
become automatic permission to apply. Adding analysis/intelligence must not
change how often a job is notified (once per capture, none for duplicates, none
for silent auto-fetch captures).
"""
import os
import tempfile
import unittest
from unittest import mock

from config import Config

# Switch app settings BEFORE importing main/database.
_tmp = tempfile.mkdtemp(prefix="jobsafety_")
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
        "company": "Acme Soft",
        "location": "Chennai",
        "url": "http://ex.com/safety",
        "description": "FastAPI and PostgreSQL service work.",
        "emails": ["hr@acme.in"],
        "phones": [],
        "experience": "0-1 years",
        "salary": "",
        "source": "linkedin",
        "apply_link": "",
    }
    base.update(kw)
    return JobCreate(**base)


_MATCHED = {"intelligence_status": "ok", "match_status": "matched", "match_score": 95,
            "llm_enriched": False, "reasons": ["Great fit"], "concerns": []}
_NOT_MATCHED = {"intelligence_status": "ok", "match_status": "not_matched",
                "match_score": 5, "llm_enriched": False,
                "reasons": [], "concerns": ["Role is not in the target roles"]}


class AutoApplySafetyTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.db.query(Application).delete()
        self.db.query(Setting).delete()
        self.db.query(Job).delete()
        self.db.commit()
        self.addCleanup(self.db.close)

    def _set(self, key, value):
        self.db.query(Setting).filter(Setting.key == key).delete()
        self.db.add(Setting(key=key, value=value))
        self.db.commit()

    def _reload(self, job_id):
        self.db.expire_all()
        return self.db.query(Job).filter(Job.id == job_id).first()

    def test_negative_intelligence_never_blocks_eligible_email(self):
        # A 'not_matched' verdict must NOT stop an eligible email auto-apply.
        # The existing gates (auto_apply setting, confidence, <=1yr experience,
        # has_email, duplicate checks) are the ONLY authority.
        self._set("auto_apply", "1")
        self._set("confirm_before_send", "0")
        with mock.patch("services.job_intelligence.evaluate_job",
                        return_value=_NOT_MATCHED), \
             mock.patch("routes.jobs._send_application_with_retry") as send:
            send.return_value = {"success": True, "to_email": "hr@acme.in",
                                 "message_id": "m1", "resume_used": "resume.pdf"}
            job = ingest_job(self.db, _job(url="http://ex.com/low-score-email"))
        row = self._reload(job["id"])
        self.assertEqual(row.status, "applied")
        self.assertEqual(row.job_intelligence["match_status"], "not_matched")
        app = self.db.query(Application).filter(Application.job_id == row.id).first()
        self.assertIsNotNone(app)
        self.assertEqual(app.type, "email")

    def test_positive_intelligence_never_bypasses_experience_gate(self):
        # A glowing 'matched' verdict must NOT auto-apply a >1yr role.
        self._set("auto_apply", "1")
        self._set("confirm_before_send", "0")
        with mock.patch("services.job_intelligence.evaluate_job",
                        return_value=_MATCHED), \
             mock.patch("routes.jobs._send_application_with_retry") as send:
            job = ingest_job(self.db, _job(url="http://ex.com/high-score-senior",
                                           experience="3-5 years",
                                           description="Senior engineer role."))
        row = self._reload(job["id"])
        self.assertEqual(row.status, "pending")
        send.assert_not_called()
        self.assertIsNone(self.db.query(Application)
                          .filter(Application.job_id == row.id).first())

    def test_confirm_gate_untouched_by_intelligence(self):
        self._set("auto_apply", "1")
        self._set("confirm_before_send", "1")
        with mock.patch("services.job_intelligence.evaluate_job",
                        return_value=_MATCHED), \
             mock.patch("routes.jobs._send_application_with_retry") as send:
            job = ingest_job(self.db, _job(url="http://ex.com/confirm-email"))
        row = self._reload(job["id"])
        self.assertEqual(row.status, "ready_to_send")
        send.assert_not_called()
        self.assertIsNone(self.db.query(Application)
                          .filter(Application.job_id == row.id).first())

    def test_duplicate_email_guard_untouched_by_intelligence(self):
        self._set("auto_apply", "1")
        self._set("confirm_before_send", "0")
        with mock.patch("services.job_intelligence.evaluate_job",
                        return_value=_MATCHED), \
             mock.patch("routes.jobs._email_previously_sent", return_value=True), \
             mock.patch("routes.jobs._send_application_with_retry") as send:
            job = ingest_job(self.db, _job(url="http://ex.com/dup-email"))
        row = self._reload(job["id"])
        self.assertEqual(row.status, "duplicate")
        send.assert_not_called()
        self.assertIsNone(self.db.query(Application)
                          .filter(Application.job_id == row.id).first())

    def test_phone_summary_path_untouched(self):
        # Phone-only job still routes to a phone summary (no email auto-apply),
        # independent of intelligence.
        self._set("auto_apply", "1")
        self._set("confirm_before_send", "0")
        with mock.patch("services.job_intelligence.evaluate_job",
                        return_value=_NOT_MATCHED), \
             mock.patch("routes.jobs.EmailBuilder") as builder:
            inst = builder.return_value
            inst.send_phone_summary.return_value = {"success": True,
                                                    "to_email": "me@example.com",
                                                    "message_id": "p1"}
            job = ingest_job(self.db, _job(url="http://ex.com/phone-only",
                                           emails=[], phones=["9894593190"],
                                           apply_link=""))
        row = self._reload(job["id"])
        self.assertEqual(row.status, "phone_summary_sent")
        builder.assert_called_once()
        inst.send_phone_summary.assert_called_once()
        app = self.db.query(Application).filter(Application.job_id == row.id).first()
        self.assertIsNotNone(app)
        self.assertEqual(app.type, "phone_summary")


class NotificationSafetyTests(unittest.TestCase):
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

    def test_notification_fires_once_per_job_and_not_for_duplicates(self):
        with mock.patch("services.notify.push") as push:
            first = ingest_job(self.db, _job(url="http://ex.com/notif-1", emails=[]))
            second = ingest_job(self.db, _job(url="http://ex.com/notif-1", emails=[]))
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(push.call_count, 1)

    def test_auto_fetch_silent_captures_send_no_per_job_push(self):
        with mock.patch("services.notify.push") as push:
            ingest_job(self.db, _job(url="http://ex.com/notif-silent", emails=[]),
                       silent_push=True)
        push.assert_not_called()

    def test_capture_and_fetch_use_one_notification_path(self):
        # A normal capture pushes once; the auto-fetch path never double-pushes
        # because it is silent per job (fetch_jobs_now batches one summary push).
        with mock.patch("services.notify.push") as push:
            captured = ingest_job(self.db, _job(url="http://ex.com/cap-1", emails=[]))
            fetched = ingest_job(self.db, _job(url="http://ex.com/fetch-1", emails=[]),
                                 silent_push=True)
        self.assertEqual(push.call_count, 1)


if __name__ == "__main__":
    unittest.main()