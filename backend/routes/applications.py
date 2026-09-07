from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models import Application, Job


router = APIRouter(prefix="/api", tags=["applications"])


class ApplicationStatusUpdate(BaseModel):
    status: str


@router.get("/applications")
def list_applications(db: Session = Depends(get_db)):
    apps = db.query(Application).order_by(Application.created_at.desc()).all()
    result = []
    for app in apps:
        item = app.to_dict()
        job = db.query(Job).filter(Job.id == app.job_id).first()
        if job:
            item["job_title"] = job.title
            item["company"] = job.company
        result.append(item)
    return result


@router.get("/applications/{app_id}")
def get_application(app_id: int, db: Session = Depends(get_db)):
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(404, "Application not found")
    return app.to_dict()


@router.put("/applications/{app_id}")
def update_application(app_id: int, update: ApplicationStatusUpdate, db: Session = Depends(get_db)):
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(404, "Application not found")
    app.status = update.status
    db.commit()
    return app.to_dict()


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    from routes.jobs import cleanup_no_contact
    cleanup_no_contact(db)
    total_jobs = db.query(Job).count()
    applied = db.query(Job).filter(Job.status == "applied").count()
    pending = db.query(Job).filter(Job.status == "pending").count()
    no_contact = db.query(Job).filter(Job.status == "no_contact").count()
    duplicate = db.query(Job).filter(Job.status == "duplicate").count()
    summary_sent = db.query(Job).filter(Job.status == "phone_summary_sent").count()

    return {
        "total_jobs": total_jobs,
        "applied": applied,
        "pending": pending,
        "no_contact": no_contact,
        "duplicate": duplicate,
        "phone_summaries_sent": summary_sent,
        "phone_only": no_contact,
    }