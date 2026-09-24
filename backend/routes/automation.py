"""Automation / Workflow observability (Phase 7).

Read-ONLY diagnostics for the dashboard's Automation view. This router never
mutates settings, never triggers fetches or workers, and never returns secrets.
All booleans/counters come straight from config presence + the DB.

One endpoint: GET /api/automation/status -> {readiness, sources, agents,
workflows, recent_activity}. It also owns the shared `readiness_body()` used by
the (unchanged, auth-gated) GET /api/readiness so the two never diverge.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import Config
from database import engine, get_db, SessionLocal
from models import Job, Setting
from services import job_search as _js
from services.activity import latest_runs, latest_run
from routes.jobs import _get_setting


router = APIRouter(prefix="/api", tags=["automation"])


def _cfg_enabled(value) -> bool:
    """Same truthiness the ingestion gates use for the *ENABLED flags."""
    return str(value or "").lower() not in ("0", "false", "no", "")


def _jsonx(column, key):
    """JSON field extraction that works on both SQLite (json_extract) and
    PostgreSQL (->>) with the SAME dashboards-visible key name."""
    if engine.dialect.name == "sqlite":
        return func.json_extract(column, "$." + key)
    return column.op("->>")(key)


def readiness_body() -> dict:
    """Configuration presence check — BOOLEANS ONLY, never secrets/keys/URLs."""
    db_ok = False
    try:
        from sqlalchemy import text as _sql

        with engine.connect() as _conn:
            _conn.execute(_sql("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    auto_fetch = False
    try:
        db = SessionLocal()
        try:
            auto_fetch = _get_setting(db, "auto_fetch", "0") in ("1", "true", "yes")
        finally:
            db.close()
    except Exception:
        pass

    return {
        "app": Config.APP_NAME,
        "status": "ok" if db_ok else "degraded",
        "database_configured": db_ok,
        "temporal_configured": bool((Config.TEMPORAL_ADDRESS or "").strip()),
        "job_analysis_enabled": _cfg_enabled(Config.JOB_ANALYSIS_ENABLED),
        "job_intelligence_enabled": _cfg_enabled(Config.JOB_INTELLIGENCE_ENABLED),
        "groq_configured": bool((Config.GROQ_API_KEY or "").strip()),
        "adzuna_configured": _js.adzuna_configured(),
        "google_rss_enabled": _cfg_enabled(Config.GOOGLE_RSS_ENABLED),
        "dashboard_auto_fetch": auto_fetch,
    }


def _run_groups(db: Session, workflow: str = "external_job_fetch"):
    """Group per-fetch rows by their started_at stamp (one fetch = one adzuna
    row + one google_rss row) so 'runs' = distinct real runs, not rows."""
    groups = {}
    for r in latest_runs(db, workflow=workflow, limit=100):
        key = r["started_at"] or r["created_at"] or r["completed_at"]
        g = groups.setdefault(key, {
            "found": 0, "ingested": 0, "status": "completed",
            "completed_at": r["completed_at"] or r["created_at"],
        })
        g["found"] += r["items_found"] or 0
        g["ingested"] += r["items_ingested"] or 0
        if r["status"] == "failed":
            g["status"] = "failed"
    ordered = sorted(groups.values(), key=lambda g: g["completed_at"] or "", reverse=True)
    return ordered


def _agent_metrics(db: Session, json_col, status_key, ts_key):
    """Counts + last-activity timestamps from per-job JSON enrichment. These ARE
    persistent (stored on each job row); not fabricated frontend statistics."""
    ok_expr = _jsonx(json_col, status_key) == "ok"
    counts = dict(
        ok=db.query(func.count(Job.id)).filter(ok_expr).scalar() or 0
    )
    counts["total"] = db.query(func.count(Job.id)).filter(
        _jsonx(json_col, status_key).isnot(None)).scalar() or 0
    counts["failed"] = db.query(func.count(Job.id)).filter(
        _jsonx(json_col, status_key) == "failed").scalar() or 0
    counts["last_activity"] = db.query(func.max(_jsonx(json_col, ts_key))).filter(
        ok_expr).scalar()
    return counts


def _source_activity(db: Session) -> dict:
    total = db.query(func.count(Job.id)).scalar() or 0
    auto = db.query(func.count(Job.id)).filter(Job.source == "auto_fetch").scalar() or 0
    last_capture = db.query(func.max(Job.created_at)).filter(
        func.coalesce(Job.source, "linkedin") != "auto_fetch").scalar()

    adzuna_run = latest_run(db, source="adzuna")
    rss_run = latest_run(db, source="google_rss")

    return {
        "linkedin": {
            "type": "Event-driven",
            "status": "Available",  # ingest pipeline confirmed; no device heartbeat
            "captures_total": total - auto,
            "last_capture_at": last_capture.isoformat() if last_capture else None,
        },
        "google_rss": {
            "enabled": _cfg_enabled(Config.GOOGLE_RSS_ENABLED),
            "last_fetch_at": (rss_run or {}).get("completed_at"),
            "jobs_found": (rss_run or {}).get("items_found", 0),
            "jobs_ingested": (rss_run or {}).get("items_ingested", 0),
            "last_status": (rss_run or {}).get("status", "no_run"),
        },
        "adzuna": {
            "configured": _js.adzuna_configured(),
            "last_fetch_at": (adzuna_run or {}).get("completed_at"),
            "jobs_found": (adzuna_run or {}).get("items_found", 0),
            "jobs_ingested": (adzuna_run or {}).get("items_ingested", 0),
            "last_status": (adzuna_run or {}).get("status", "no_run"),
            "api_calls_total": int(_get_setting(db, "fetch_api_calls", "0") or "0"),
        },
    }


def _agent_activity(db: Session) -> dict:
    anal = _agent_metrics(db, Job.job_analysis, "analysis_status", "analyzed_at")
    intel = _agent_metrics(db, Job.job_intelligence, "intelligence_status", "evaluated_at")
    intel_statuses = {
        s: db.query(func.count(Job.id)).filter(
            _jsonx(Job.job_intelligence, "match_status") == s).scalar() or 0
        for s in ("matched", "needs_review", "not_matched", "unavailable")
    }
    return {
        "job_analysis": {
            "enabled": _cfg_enabled(Config.JOB_ANALYSIS_ENABLED),
            "llm_enabled": _cfg_enabled(Config.JOB_ANALYSIS_USE_LLM),
            "jobs_analyzed": anal["ok"],
            "failures": anal["failed"],
            "last_activity": anal["last_activity"],
        },
        "job_intelligence": {
            "enabled": _cfg_enabled(Config.JOB_INTELLIGENCE_ENABLED),
            "llm_enabled": _cfg_enabled(Config.JOB_INTELLIGENCE_USE_LLM),
            "jobs_evaluated": intel["ok"],
            "failures": intel["failed"],
            "last_activity": intel["last_activity"],
            **intel_statuses,
        },
    }


def _workflow_summary(db: Session) -> dict:
    groups = _run_groups(db)
    temporal = bool((Config.TEMPORAL_ADDRESS or "").strip())
    latest = groups[0] if groups else None
    return {
        "external_job_fetch": {
            "name": "External Job Fetch",
            "status": latest["status"] if latest
                      else ("never" if _js.adzuna_configured() else "not_configured"),
            "last_run": latest["completed_at"] if latest else None,
            "last_found": latest["found"] if latest else 0,
            "last_ingested": latest["ingested"] if latest else 0,
            "runs_total": len(groups),
            "next_run": None,  # not persisted anywhere — show 'Not available'
            "frequency": ("Every 12 hours (Temporal schedule)"
                          if temporal else "Once daily (built-in scheduler)"),
            "handled_by_temporal": temporal,
        }
    }


def _recent_activity(db: Session, limit: int = 12) -> list:
    items = []
    # 1) External fetch runs (one combined line per real run, newest first).
    for g in _run_groups(db)[:6]:
        if g["status"] == "completed":
            items.append({
                "level": "ok",
                "text": f"External job fetch completed — {g['found']} jobs found · {g['ingested']} new",
                "time": g["completed_at"],
            })
        else:
            items.append({
                "level": "warn",
                "text": "External job fetch failed",
                "time": g["completed_at"],
            })
    # 2) LinkedIn / mobile captures (real job rows, newest first).
    captures = db.query(Job).filter(
        func.coalesce(Job.source, "linkedin") != "auto_fetch"
    ).order_by(Job.created_at.desc()).limit(6).all()
    for j in captures:
        title = (j.title or "Untitled").replace("|", "/")
        items.append({
            "level": "info",
            "text": f"Job captured ({j.source or 'linkedin'}): {title}"
                    + (f" @ {j.company}" if j.company else ""),
            "time": j.created_at.isoformat() if j.created_at else None,
        })
    # 3) Settings-originated facts we can prove (no fabrication).
    last_summary = _get_setting(db, "last_fetch_summary", "")
    if last_summary and all(i["text"] != last_summary for i in items):
        items.append({"level": "ok", "text": last_summary,
                      "time": _get_setting(db, "last_fetch_at", "") or None})
    items.sort(key=lambda i: i["time"] or "", reverse=True)
    return items[:limit]


@router.get("/automation/status", include_in_schema=False)
def automation_status(db: Session = Depends(get_db)):
    """Read-only operational snapshot for the dashboard Automation view.
    Never returns secrets: no keys, URLs, credentials, or env dumps."""
    return {
        "readiness": readiness_body(),
        "sources": _source_activity(db),
        "agents": _agent_activity(db),
        "workflows": _workflow_summary(db),
        "recent_activity": _recent_activity(db),
    }