"""Render cron job: daily housekeeping.

Runs through the same cleanup the app startup performs periodically (purge
stale no-contact rows older than 24h) and logs a one-line stats summary so
Render's cron logs double as an at-a-glance health report.

Set the schedule in backend/render.yaml (e.g. `0 4 * * *` = daily 04:00 UTC).
"""
import json
import sys
import time

from database import SessionLocal
from routes.jobs import cleanup_no_contact
from models import Job

STATUSES = [
    "pending",
    "ready_to_send",
    "applied",
    "phone_summary_sent",
    "link_email_sent",
    "duplicate",
    "no_contact",
]


def run() -> None:
    start = time.time()
    db = SessionLocal()
    try:
        deleted = cleanup_no_contact(db, max_age_hours=24)
        counts = {s: db.query(Job).filter(Job.status == s).count() for s in STATUSES}
        print(json.dumps({
            "cron": "housekeeping",
            "deleted_no_contact": deleted,
            "jobs": counts,
            "seconds": round(time.time() - start, 2),
        }, ensure_ascii=False))
    except Exception as exc:
        print(f"CRON ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()