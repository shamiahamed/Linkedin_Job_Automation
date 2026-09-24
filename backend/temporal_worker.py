"""Temporal worker entrypoint — scheduled Google RSS + Adzuna fetch (Phase 3).

Runs as a dedicated Render 'worker' service (see backend/render.yaml). When the
Temporal Cloud connection vars are present it:
  1. connects to the Temporal namespace (TLS is implied by the API key),
  2. idempotently registers the "every 12h" Schedule,
  3. runs the worker that executes FetchJobsWorkflow / run_fetch_activity.

When TEMPORAL_ADDRESS is ABSENT it prints a notice and exits cleanly — the
built-in main.py daily-fetch scheduler keeps doing the job, so deploying this
service without Temporal settings is a harmless no-op.

temporalio is imported lazily so this file (and the rest of the app) imports
even on machines without the package installed.
"""
import asyncio
import logging

from config import Config

logger = logging.getLogger("uvicorn.error")

SCHEDULE_ID = "job-auto-apply-fetch-every-12h"


def temporal_configured() -> bool:
    """True when the worker can try to connect to Temporal Cloud."""
    return bool((Config.TEMPORAL_ADDRESS or "").strip())


async def _connect():
    from temporalio.client import Client

    return await Client.connect(
        Config.TEMPORAL_ADDRESS.strip(),
        namespace=(Config.TEMPORAL_NAMESPACE or "default"),
        api_key=(Config.TEMPORAL_API_KEY or "").strip() or None,
    )


async def _ensure_schedule(client) -> None:
    """Idempotent: create the recurring fetch Schedule once per namespace."""
    from datetime import timedelta
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleIntervalSpec,
        ScheduleSpec,
        ScheduleState,
        ScheduleAlreadyRunningError,
    )
    from temporal_workflows import FetchJobsWorkflow

    interval_hours = max(1, int(Config.TEMPORAL_FETCH_INTERVAL_HOURS or "12"))
    schedule = Schedule(
        action=ScheduleActionStartWorkflow(
            FetchJobsWorkflow.run,
            {"source": "temporal_schedule"},
            id="fetch-jobs-every-12h",
            task_queue=Config.TEMPORAL_TASK_QUEUE,
        ),
        spec=ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(hours=interval_hours))]
        ),
        state=ScheduleState(
            note="Google RSS + Adzuna scheduled fetch via Temporal (Phase 3)."
        ),
    )
    try:
        await client.create_schedule(SCHEDULE_ID, schedule)
        logger.info("Temporal schedule '%s' created (every %dh)", SCHEDULE_ID, interval_hours)
    except ScheduleAlreadyRunningError:
        logger.info("Temporal schedule '%s' already exists — skipping creation", SCHEDULE_ID)


async def _run_worker() -> None:
    from temporalio.worker import Worker
    from temporal_workflows import FetchJobsWorkflow, run_fetch_activity

    client = await _connect()
    await _ensure_schedule(client)
    worker = Worker(
        client,
        task_queue=Config.TEMPORAL_TASK_QUEUE,
        workflows=[FetchJobsWorkflow],
        activities=[run_fetch_activity],
    )
    logger.info("Temporal worker listening on queue '%s' ...",
                Config.TEMPORAL_TASK_QUEUE)
    await worker.run()


async def main() -> None:
    if not temporal_configured():
        # Fall back to the built-in main.py scheduler — deploying this service
        # without Temporal settings is a clean no-op.
        import sys
        print("TEMPORAL_ADDRESS not set — Temporal worker idle; "
              "the built-in main.py scheduler remains in charge.")
        sys.exit(0)
    try:
        await _run_worker()
    except Exception:
        logger.exception("Temporal worker failed")
        raise


if __name__ == "__main__":
    asyncio.run(main())