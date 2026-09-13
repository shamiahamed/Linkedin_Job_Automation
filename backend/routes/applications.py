from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
from database import get_db
from models import Application, Job
from routes.jobs import _get_setting


router = APIRouter(prefix="/api", tags=["applications"])


class ApplicationUpdate(BaseModel):
    status: Optional[str] = None
    outcome: Optional[str] = None
    follow_up_at: Optional[str] = None
    notes: Optional[str] = None


OUTCOMES = {"interview", "rejected", "offer", "ghosted"}


@router.get("/applications")
def list_applications(job_id: Optional[int] = None, db: Session = Depends(get_db)):
    apps = db.query(Application).order_by(Application.created_at.desc()).all()
    if job_id is not None:
        apps = [a for a in apps if a.job_id == job_id]
    followup_days = int(_get_setting(db, "followup_days", "3") or "3")
    now = datetime.utcnow()
    result = []
    for app in apps:
        item = app.to_dict()
        job = db.query(Job).filter(Job.id == app.job_id).first()
        if job:
            item["job_title"] = job.title
            item["company"] = job.company
            item["job_status"] = job.status
        item["followup_due"] = bool(
            app.type == "email"
            and app.status == "sent"
            and not app.followed_up_at
            and not app.outcome
            and app.created_at
            and (app.created_at + timedelta(days=followup_days) < now)
        )
        result.append(item)
    return result


@router.get("/applications/{app_id}")
def get_application(app_id: int, db: Session = Depends(get_db)):
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(404, "Application not found")
    item = app.to_dict()
    job = db.query(Job).filter(Job.id == app.job_id).first()
    if job:
        item["job_title"] = job.title
        item["company"] = job.company
    return item


@router.put("/applications/{app_id}")
def update_application(app_id: int, update: ApplicationUpdate, db: Session = Depends(get_db)):
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(404, "Application not found")
    if update.status is not None:
        app.status = update.status
    if update.outcome is not None:
        if update.outcome and update.outcome not in OUTCOMES:
            raise HTTPException(400, f"outcome must be one of {sorted(OUTCOMES)}")
        app.outcome = update.outcome or None  # "" / null clears the outcome
        if update.outcome in ("rejected", "offer") and app.outcome:
            # Final states: no further follow-ups needed.
            app.followed_up_at = app.followed_up_at or datetime.utcnow()
    if update.follow_up_at is not None:
        try:
            app.follow_up_at = datetime.fromisoformat(update.follow_up_at) if update.follow_up_at else None
        except ValueError:
            raise HTTPException(400, "follow_up_at must be ISO datetime")
    if update.notes is not None:
        app.notes = update.notes
    db.commit()
    db.refresh(app)
    return app.to_dict()


@router.post("/applications/{app_id}/followup")
def send_followup(app_id: int, db: Session = Depends(get_db)):
    """AI-draft and send a polite follow-up to the original recruiter email for an
    already-sent application. Recorded as a separate 'followup' application row
    (never blocked by the duplicate-email guard) and marks the original as done."""
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(404, "Application not found")
    job = db.query(Job).filter(Job.id == app.job_id).first()
    if not job or not job.emails:
        raise HTTPException(400, "This application has no recruiter email to follow up on.")
    if app.type != "email":
        raise HTTPException(400, "Only recruiter-email applications can be followed up.")

    from services import llm
    from services.email_sender import EmailSender
    from html import escape

    applied_on = app.created_at.strftime("%d %b %Y") if app.created_at else ""
    body = llm.draft_followup(job, applied_on)
    if not body:
        body = (
            "Dear HR,\n\n"
            f"I submitted my application for the {job.title} position at "
            f"{job.company or 'your company'} on {applied_on or 'recently'} and I remain very "
            f"interested in the role. I would appreciate any update on my application status.\n\n"
            "Thank you for your time."
        )

    def _h(v):
        return escape(str(v or ""))

    from config import Config

    paragraphs = [p.strip() for p in body.split("\n") if p.strip()]
    if not paragraphs[0].lower().startswith("dear"):
        paragraphs.insert(0, "Dear HR,")
    html = f"""
<html><body style="font-family:Arial,Helvetica,sans-serif;color:#333;line-height:1.6;max-width:600px;margin:0 auto;padding:20px;">
  <div style="background:#f4f6f8;padding:20px;border-radius:8px;border:1px solid #e0e0e0;">
    {''.join(f'<p>{_h(p)}</p>' for p in paragraphs)}
    <br>
    <p>Best regards,</p>
    <p><strong>{_h(Config.YOUR_NAME)}</strong></p>
    <p>✉️ {_h(Config.YOUR_EMAIL)}</p>
  </div>
</body></html>"""

    to_email = job.emails[0]
    sender = EmailSender()
    result = sender.send_email(
        to_email=to_email,
        subject=f"Re: Application for {job.title} - {Config.YOUR_NAME}",
        html_content=html,
        to_name=job.company or "",
    )
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "follow-up send failed")}

    # Original application is now followed up on.
    app.followed_up_at = datetime.utcnow()
    app.status = "followed_up"
    db.add(Application(
        job_id=job.id,
        email_sent_to=to_email,
        email_response=result.get("message_id"),
        type="followup",
        status="sent",
    ))
    job.status = "applied"
    db.commit()

    try:
        from services.notify import push as _push

        _push("Follow-up sent ➤", f"{job.title} at {job.company or 'unknown'}", "/dashboard")
    except Exception:
        pass
    return {"success": True, "to_email": to_email, "message_id": result.get("message_id")}