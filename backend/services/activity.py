"""Tiny activity ledger for the Automation view (Phase 7).

Purpose: persist a small, safe record of workflow/agent activity so the read-only
dashboard can show REAL history (last-run, counts, per-source fetch results)
instead of invented values. This module is deliberately decoupled from the
routes layer so both `routes/jobs` (writer) and `routes/automation` (reader)
can share it without circular imports.

Security: only counts + short status strings are stored. Never job content,
secrets, API keys, full exception traces, or LLM prompts/responses.
"""
from datetime import datetime

from models import AutomationRun


def record_run(db, workflow: str, source: str, status: str = "completed",
               items_found: int = 0, items_ingested: int = 0,
               error_summary: str = "", started_at: datetime = None,
               completed_at: datetime = None, keep: int = 200) -> None:
    """Append one activity row and prune old ones so the table stays tiny.

    Idempotent with respect to the caller (each workflow run records at most a
    handful of rows). Never raises: observability must never break ingestion.
    """
    if keep < 10:
        keep = 10
    try:
        now = datetime.utcnow()
        db.add(AutomationRun(
            workflow=workflow,
            source=source,
            status=status,
            started_at=started_at or now,
            completed_at=completed_at or now,
            items_found=max(0, int(items_found or 0)),
            items_ingested=max(0, int(items_ingested or 0)),
            error_summary=(error_summary or "")[:255],
        ))
        # Small housekeeping: drop rows older than the newest `keep`.
        latest = db.query(AutomationRun.id).order_by(AutomationRun.id.desc()).offset(keep).first()
        if latest:
            too_old = db.query(AutomationRun).filter(AutomationRun.id <= latest[0])
            for r in too_old.all():
                db.delete(r)
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def latest_runs(db, workflow: str = None, source: str = None, limit: int = 10) -> list:
    """Newest AutomationRun rows (optionally filtered), as plain dicts."""
    q = db.query(AutomationRun)
    if workflow:
        q = q.filter(AutomationRun.workflow == workflow)
    if source:
        q = q.filter(AutomationRun.source == source)
    return [r.to_dict() for r in q.order_by(AutomationRun.id.desc()).limit(limit).all()]


def latest_run(db, source: str) -> dict:
    """Most recent single row for a source, or None."""
    rows = latest_runs(db, source=source, limit=1)
    return rows[0] if rows else None