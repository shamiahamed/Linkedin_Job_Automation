from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, JSON, ForeignKey
from sqlalchemy.sql import func
from database import Base


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    company = Column(String(255), nullable=True)
    location = Column(String(255), nullable=True)
    url = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    emails = Column(JSON, default=list)
    phones = Column(JSON, default=list)
    experience = Column(String(255), nullable=True)
    salary = Column(String(100), nullable=True)
    source = Column(String(50), default="linkedin")
    apply_link = Column(String(500), nullable=True)
    has_email = Column(Boolean, default=False)
    has_phone = Column(Boolean, default=False)
    status = Column(String(20), default="pending")
    # User-saved (⭐) — saved jobs are exempt from auto-delete/2-day fetch purge.
    saved = Column(Boolean, default=False)
    # Auto-fetch run marker (timestamp) so the last N fetch batches can be
    # identified and deleted together from the dashboard.
    fetch_batch = Column(String(50), nullable=True)
    # Job Analysis Agent output (JSON dict; see services/job_analyzer.py schema).
    # Nullable: existing/pre-Phase-2 jobs simply have none. Purely informational
    # enrichment — the analyzer NEVER affects auto-apply/email/notify/dedupe.
    job_analysis = Column(JSON, nullable=True)
    # Job Intelligence Agent output (JSON dict; see services/job_intelligence.py
    # schema). Nullable: existing jobs / jobs captured while disabled have none.
    # Purely informational enrichment — never affects any downstream automation.
    job_intelligence = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "description": self.description,
            "emails": self.emails or [],
            "phones": self.phones or [],
            "experience": self.experience,
            "salary": self.salary,
            "source": self.source,
            "apply_link": self.apply_link,
            "has_email": self.has_email,
            "has_phone": self.has_phone,
            "status": self.status,
            "saved": bool(self.saved),
            "fetch_batch": self.fetch_batch,
            "job_analysis": self.job_analysis,
            "job_intelligence": self.job_intelligence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"))
    resume_used = Column(String(255), nullable=True)
    email_sent_to = Column(String(255), nullable=True)
    email_response = Column(Text, nullable=True)
    type = Column(String(20), default="email")
    status = Column(String(20), default="sent")
    follow_up_at = Column(DateTime(timezone=True), nullable=True)
    followed_up_at = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(String(20), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "job_id": self.job_id,
            "resume_used": self.resume_used,
            "email_sent_to": self.email_sent_to,
            "email_response": self.email_response,
            "type": self.type,
            "status": self.status,
            "follow_up_at": self.follow_up_at.isoformat() if self.follow_up_at else None,
            "followed_up_at": self.followed_up_at.isoformat() if self.followed_up_at else None,
            "outcome": self.outcome,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)
    key = Column(String(50), nullable=False, unique=True)
    value = Column(String(255), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Resume(Base):
    """Uploaded resume files stored as base64 data (DB-backed so they survive
    both local SQLite and the stateless Render/Neon deployment)."""

    __tablename__ = "resumes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    data = Column(Text, nullable=False)  # base64-encoded file bytes
    mime = Column(String(50), default="application/pdf")
    label = Column(String(255), nullable=True)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "mime": self.mime,
            "label": self.label,
            "is_default": self.is_default,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PushSubscription(Base):
    """Browser/service-worker push subscriptions for mobile + desktop
    notifications (Web Push). Registered from the dashboard PWA."""

    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    endpoint = Column(String(500), nullable=False, unique=True)
    p256dh = Column(String(255), nullable=False)
    auth = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "endpoint": self.endpoint,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AutomationRun(Base):
    """Lightweight, persistent record of a workflow/agent activity tick (Phase 7).

    Read-only observability: the dashboard Automation view surfaces the last few
    rows, the frontend NEVER writes here. Deliberately small/anonymised on
    purpose — it stores counts and short status strings only, never job
    descriptions, prompts/responses, secrets, API keys, or exception traces.
    Currently the only recorded workflow is the external job fetch (Adzuna +
    Google RSS); LinkedIn/mobile captures are derived from the jobs table
    directly, so this table stays tiny."""

    __tablename__ = "automation_runs"

    id = Column(Integer, primary_key=True, index=True)
    workflow = Column(String(50), nullable=False)   # e.g. "external_job_fetch"
    source = Column(String(50), nullable=False)     # e.g. "adzuna" / "google_rss"
    status = Column(String(20), nullable=False, default="completed")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    items_found = Column(Integer, default=0)
    items_ingested = Column(Integer, default=0)
    error_summary = Column(String(255), nullable=True, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "workflow": self.workflow,
            "source": self.source,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "items_found": self.items_found,
            "items_ingested": self.items_ingested,
            "error_summary": self.error_summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
