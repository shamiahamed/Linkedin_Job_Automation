"""Phase 3 tests — Temporal worker delegation of the scheduled fetch.

Strategy: the hard logic (gate + fetch reuse, hand-over decision, worker
no-op) is all importable WITHOUT the temporalio SDK, so the suite runs on any
machine even before `pip install temporalio`. The async activity wrapper test
is skipped when the SDK isn't installed.

DB pattern mirrors test_job_analyzer_ingest: Config.DATABASE_URL is switched
BEFORE `database`/`main` are imported so SessionLocal hits a throwaway SQLite
DB; auto-apply stays off so nothing is ever emailed/auto-applied.
"""
import asyncio
import importlib
import os
import tempfile
import unittest
from unittest import mock

from config import Config

# Switch app settings BEFORE importing main/database.
_tmp = tempfile.mkdtemp(prefix="jobtemporal_")
Config.DATABASE_URL = "sqlite:///" + os.path.join(_tmp, "test.db").replace(os.sep, "/")
Config.API_TOKEN = ""
Config.APP_PASSWORD = ""
Config.GROQ_API_KEY = ""
Config.TEMPORAL_ADDRESS = ""

from database import SessionLocal  # noqa: E402
from models import Job, Application, Setting  # noqa: E402
from services.scheduled_fetch import run_scheduled_fetch  # noqa: E402
from temporal_worker import temporal_configured  # noqa: E402
import main as app_module  # noqa: E402  (registers temporal_handles_fetch, creates tables)


def _temporalio_available() -> bool:
    try:
        import temporalio  # noqa: F401
        return True
    except ImportError:
        return False


class ScheduledFetchGateTests(unittest.TestCase):
    """run_scheduled_fetch — the exact gate/run the Temporal activity executes."""

    def setUp(self):
        self.db = SessionLocal()
        self.db.query(Application).delete()
        self.db.query(Setting).delete()
        self.db.query(Job).delete()
        self.db.add(Setting(key="auto_apply", value="0"))
        self.db.commit()
        self.addCleanup(self.db.close)

    def _set_auto_fetch(self, value):
        self.db.add(Setting(key="auto_fetch", value=value))
        self.db.commit()

    def test_auto_fetch_off_skips_without_fetching(self):
        self._set_auto_fetch("0")
        with mock.patch("routes.jobs.fetch_jobs_now") as mocked:
            result = run_scheduled_fetch(self.db)
        self.assertEqual(result["skipped"], True)
        self.assertEqual(result["success"], False)
        mocked.assert_not_called()

    def test_auto_fetch_on_runs_fetch_and_returns_its_result(self):
        self._set_auto_fetch("1")
        sentinel = {"success": True, "fetched": 4, "added": 2}
        with mock.patch("routes.jobs.fetch_jobs_now", return_value=sentinel) as mocked:
            result = run_scheduled_fetch(self.db)
        self.assertIs(result, sentinel)
        mocked.assert_called_once_with(self.db)

    def test_auto_fetch_string_variants_are_honoured(self):
        self._set_auto_fetch("yes")
        sentinel = {"success": True, "fetched": 1}
        with mock.patch("routes.jobs.fetch_jobs_now", return_value=sentinel):
            result = run_scheduled_fetch(self.db)
        self.assertEqual(result["success"], True)


@unittest.skipUnless(_temporalio_available(), "temporalio SDK not installed")
class ActivityWrapperTests(unittest.TestCase):
    """run_fetch_activity — async Temporal activity wrapping run_scheduled_fetch."""

    def setUp(self):
        self.db = SessionLocal()
        self.db.query(Application).delete()
        self.db.query(Setting).delete()
        self.db.commit()
        self.addCleanup(self.db.close)

    def test_activity_skips_when_auto_fetch_off(self):
        self.db.add(Setting(key="auto_fetch", value="0"))
        self.db.commit()
        wf = importlib.import_module("temporal_workflows")
        with mock.patch("routes.jobs.fetch_jobs_now") as mocked:
            result = asyncio.run(wf.run_fetch_activity({}))
        self.assertEqual(result["skipped"], True)
        mocked.assert_not_called()


class DelegateDecisionTests(unittest.TestCase):
    """temporal_configured() + main.temporal_handles_fetch() hand-over logic."""

    def setUp(self):
        old = Config.TEMPORAL_ADDRESS
        self.addCleanup(setattr, Config, "TEMPORAL_ADDRESS", old)

    def test_unconfigured_worker_is_idle(self):
        Config.TEMPORAL_ADDRESS = ""
        self.assertFalse(temporal_configured())
        self.assertFalse(app_module.temporal_handles_fetch())

    def test_configured_worker_takes_over(self):
        Config.TEMPORAL_ADDRESS = "ns.acct.tmprl.cloud:7233"
        self.assertTrue(temporal_configured())
        self.assertTrue(app_module.temporal_handles_fetch())

    def test_whitespace_only_address_is_unconfigured(self):
        Config.TEMPORAL_ADDRESS = "   "
        self.assertFalse(temporal_configured())
        self.assertFalse(app_module.temporal_handles_fetch())


@unittest.skipUnless(_temporalio_available(), "temporalio SDK not installed")
class WorkerEntrypointTests(unittest.TestCase):
    """backend/temporal_worker.py entrypoint behaviour."""

    def test_unconfigured_worker_exits_cleanly(self):
        worker = importlib.import_module("temporal_worker")
        with mock.patch.object(Config, "TEMPORAL_ADDRESS", ""):
            with self.assertRaises(SystemExit) as ctx:
                asyncio.run(worker.main())
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()