"""Reusable "run the scheduled fetch" wrapper (Phase 3).

Shared by the Temporal activity (temporal_workflows.run_fetch_activity) and
available for the built-in scheduler, so the gate rules live in ONE place.

Importable WITHOUT temporalio — the web app, workers, and tests can all use it
while the SDK stays an optional install.
"""


def run_scheduled_fetch(db) -> dict:
    """Run the Google RSS + Adzuna fetch through the shared ingest pipeline,
    honouring the dashboard's `auto_fetch` gate. Returns a plain result dict."""
    from routes.jobs import fetch_jobs_now, _get_setting

    if _get_setting(db, "auto_fetch", "0") not in ("1", "true", "yes"):
        return {"success": False, "skipped": True,
                "detail": "auto_fetch is off (dashboard setting)"}
    return fetch_jobs_now(db)