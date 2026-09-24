"""Temporal workflow + activity for the scheduled Google RSS + Adzuna fetch.

Phase 3 shape (per user design):
    Temporal --(every 12h schedule)--> FetchJobsWorkflow --> run_fetch_activity
                                             --> job-analysis ingest pipeline --> PostgreSQL

The WORKFLOW class is strictly deterministic — it only imports the Temporal
sandbox-safe bits and orchestrates one activity. All real work (network, DB,
dedupe, ingest through the shared job_analysis pipeline, summary push) lives in
run_fetch_activity, which executes OUTSIDE the workflow sandbox and therefore
imports heavy modules lazily inside its body.

This module is only imported when the worker is actually configured (see
temporal_worker.py), so the app never depends on temporalio being present.
"""
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

FETCH_ACTIVITY = "run_fetch_activity"
# 8 min is comfortably above the ~45s Adzuna time budget + RSS fetches.
FETCH_ACTIVITY_TIMEOUT = timedelta(minutes=8)


@workflow.defn
class FetchJobsWorkflow:
    """Scheduled Google RSS + Adzuna fetch (every 12h by default).

    Payload is informational (e.g. {"source": "temporal_schedule"}); the
    activity ignores it and reads the same settings/keys the main.py loop used.
    """

    @workflow.run
    async def run(self, payload: dict) -> dict:
        return await workflow.execute_activity(
            FETCH_ACTIVITY,
            payload,
            schedule_to_close_timeout=FETCH_ACTIVITY_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


async def run_fetch_activity(payload: dict) -> dict:
    """Activity: run the exact fetch the built-in scheduler used, honouring the
    dashboard's `auto_fetch` gate. Returns a normal result dict either way —
    only unexpected exceptions bubble up for Temporal to retry."""
    import asyncio

    from database import SessionLocal
    from services.scheduled_fetch import run_scheduled_fetch

    def _run():
        db = SessionLocal()
        try:
            return run_scheduled_fetch(db)
        finally:
            db.close()

    return await asyncio.to_thread(_run)