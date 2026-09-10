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
    created_at = Column(DateTime(timezone=True), server_default=func.now())

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
            "created_at": self.created_at.isoformat() if self.created_at else None,
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
