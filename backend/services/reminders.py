"""Daily reminder jobs (push + inbox email) driven by the background loop in main.py.

- unapplied:       "N unapplied jobs need action" (pending / ready_to_send / duplicate / apply_link)
- final-day:       "N unapplied jobs auto-delete in 24h" (older than purge_unapplied_days - 1)
- follow-up-due:   applied jobs whose recruiter email is due a polite nudge (followup_days)
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("uvicorn.error")


def _row_count(db, statuses, min_age_days: float = 0.0, max_age_days: float = None):
    from models import Job

    cutoff_min = datetime.utcnow() - timedelta(days=min_age_days)
    query = db.query(Job).filter(Job.status.in_(statuses), Job.created_at < cutoff_min)
    if max_age_days is not None:
        query = query.filter(Job.created_at >= datetime.utcnow() - timedelta(days=max_age_days))
    return query.count()


def _titles(db, statuses, min_age_days: float, limit: int = 4):
    from models import Job

    cutoff = datetime.utcnow() - timedelta(days=min_age_days)
    rows = db.query(Job).filter(Job.status.in_(statuses), Job.created_at < cutoff) \
        .order_by(Job.created_at.desc()).limit(limit).all()
    return [f"{j.title or 'Untitled'} @ {j.company or 'unknown'}" for j in rows]


def _email_user(subject: str, html_body: str) -> bool:
    try:
        from services.email_sender import EmailSender

        sender = EmailSender()
        if not sender.configured:
            return False
        from config import Config

        r = sender.send_email(
            to_email=Config.YOUR_EMAIL,
            subject=subject,
            html_content=html_body,
            to_name=Config.YOUR_NAME,
        )
        return bool(r.get("success"))
    except Exception:
        logger.exception("reminder email failed")
        return False


def _bullet(items) -> str:
    if not items:
        return ""
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def _card(title: str, msg: str) -> str:
    return f"<div style='background:#f4f6f8;padding:16px;border-radius:10px;border:1px solid #e0e0e0;max-width:560px'>" \
           f"<h3 style='margin:0 0 8px;color:#0a66c2'>{title}</h3><p style='margin:0;color:#333'>{msg}</p></div>"


def _run_unapplied(db, push, email_too=True):
    from routes.jobs import _get_setting
    from models import Job

    statuses = ["pending", "ready_to_send", "duplicate", "apply_link"]
    total = db.query(Job).filter(Job.status.in_(statuses)).count()
    if total <= 0:
        return
    items = db.query(Job).filter(Job.status.in_(statuses)).order_by(Job.created_at.desc()).limit(4).all()
    lines = [f"{j.title or 'Untitled'} @ {j.company or 'unknown'} ({j.status.replace('_', ' ')})" for j in items]
    body = f"You have {total} unapplied job" + ("s" if total != 1 else "") + " waiting. Apply them before they expire."
    push("N unapplied jobs 🔨", f"{total} unapplied job" + ("s" if total != 1 else "") + " — open the dashboard to apply.", "/dashboard")
    if email_too:
        _email_user(f"📮 {total} unapplied job" + ("s" if total != 1 else "") + " — action needed",
                    _card("Unapplied jobs", body + _bullet(lines)))


def _run_final_day(db, push, email_too=True):
    from routes.jobs import _get_setting
    from models import Job

    unapplied_days = int(_get_setting(db, "purge_unapplied_days", "14") or "14")
    statuses = ["pending", "ready_to_send", "duplicate", "apply_link"]
    n = _row_count(db, statuses, min_age_days=unapplied_days - 1, max_age_days=unapplied_days)
    if n <= 0:
        return
    items = _titles(db, statuses, min_age_days=unapplied_days - 1)
    push("⚠️ Auto-delete in 24h",
         f"{n} job" + ("s" if n != 1 else "") + " will be permanently deleted tomorrow. "
         "Apply or save them now.", "/dashboard")
    if email_too:
        _email_user(f"⚠️ {n} job" + ("s" if n != 1 else "") + " auto-delete tomorrow",
                    _card("Final-day warning", f"These will be removed in 24h. Apply them in the dashboard if you still want them."
                          + _bullet(items)))


def _run_followup_due(db, push, email_too=True):
    from routes.jobs import _get_setting
    from models import Application, Job

    followup_days = int(_get_setting(db, "followup_days", "3") or "3")
    cutoff = datetime.utcnow() - timedelta(days=followup_days)
    apps = db.query(Application).filter(
        Application.type == "email",
        Application.status == "sent",
        Application.followed_up_at.is_(None),
        Application.outcome.is_(None),
        Application.created_at < cutoff,
    ).order_by(Application.created_at.desc()).limit(4).all()
    if not apps:
        return
    lines = []
    for a in apps:
        job = db.query(Job).filter(Job.id == a.job_id).first()
        lines.append(f"{job.title if job else 'Job'} @ {(job.company if job else '') or 'unknown'}"
                     f"{' → ' + a.email_sent_to if a.email_sent_to else ''}")
    push("Follow-ups due ➤", f"{len(apps)} application" + ("s" if len(apps) != 1 else "") + " waiting for a response — send a polite nudge.", "/dashboard")
    if email_too:
        _email_user(f"➤ {len(apps)} follow-up" + ("s" if len(apps) != 1 else "") + " due today",
                    _card("Follow-ups due", "Send these applications a short follow-up note." + _bullet(lines)))


def run_daily_reminders(db):
    """Fire all daily reminders (push + inbox email). Safe to call multiple times."""
    from services.notify import push as _push

    _run_unapplied(db, _push)
    _run_final_day(db, _push)
    _run_followup_due(db, _push)


def reminders_due(db) -> bool:
    """True if today's reminders haven't fired yet and the daily schedule time
    has arrived. Uses 'on/after the scheduled local time' so the 9 AM reminder
    still fires even if the hourly loop tick misses the exact 9:00–9:59 window
    (e.g. server started at 10:30 — it now fires on the first tick after 9 AM
    instead of never)."""
    from routes.jobs import _get_setting

    if _get_setting(db, "reminders_enabled", "1") not in ("1", "true", "yes"):
        return False
    try:
        hour = int(_get_setting(db, "reminder_hour", "9") or "9")
        off = float(_get_setting(db, "reminder_offset_hours", "5.5") or "5.5")
    except (TypeError, ValueError):
        hour, off = 9, 5.5
    from datetime import timezone, timedelta as _td

    now = datetime.now(timezone(_td(hours=off)))
    today = now.strftime("%Y-%m-%d")
    if _get_setting(db, "last_reminder_date", "") == today:
        return False
    # Scheduled midnight-to-now boundary for today in local time.
    scheduled = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return now >= scheduled


def mark_reminders_done(db):
    from routes.jobs import _set_setting, _get_setting

    from datetime import timezone, timedelta as _td

    off = 5.5
    try:
        off = float(_get_setting(db, "reminder_offset_hours", "5.5") or "5.5")
    except (TypeError, ValueError):
        pass
    _set_setting(db, "last_reminder_date", datetime.now(timezone(_td(hours=off))).strftime("%Y-%m-%d"))
    db.commit()