import base64
from typing import Optional
from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import get_db
from models import Resume


router = APIRouter(prefix="/api", tags=["resumes"])


class ResumeUpdate(BaseModel):
    label: Optional[str] = None
    make_default: Optional[str] = "false"


@router.get("/resumes")
def list_resumes(db: Session = Depends(get_db)):
    rows = db.query(Resume).order_by(Resume.created_at.desc()).all()
    return [r.to_dict() for r in rows]


@router.post("/resumes")
def upload_resume(
    file: UploadFile = File(...),
    label: Optional[str] = Form(None),
    make_default: Optional[str] = Form("false"),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(400, "Missing filename")
    raw = file.file.read()
    if not raw:
        raise HTTPException(400, "Empty file")
    # Only allow PDFs to keep attachment handling predictable.
    mime = (file.content_type or "application/pdf").lower()
    if "pdf" not in mime and not file.filename.lower().endswith(".pdf"):
        mime = "application/pdf" if file.filename.lower().endswith(".pdf") else "application/pdf"

    row = Resume(
        name=file.filename,
        data=base64.b64encode(raw).decode(),
        mime="application/pdf",
        label=label or None,
        is_default=False,
    )
    db.add(row)
    if make_default.lower() in ("true", "1"):
        db.query(Resume).update({Resume.is_default: False})
        row.is_default = True
    db.commit()
    db.refresh(row)
    return row.to_dict()


@router.put("/resumes/{resume_id}")
def update_resume(resume_id: int, update: ResumeUpdate = Body(...), db: Session = Depends(get_db)):
    row = db.query(Resume).filter(Resume.id == resume_id).first()
    if not row:
        raise HTTPException(404, "Resume not found")
    if update.label is not None:
        row.label = update.label or None
    if (update.make_default or "false").lower() in ("true", "1"):
        db.query(Resume).update({Resume.is_default: False})
        row.is_default = True
    db.commit()
    db.refresh(row)
    return row.to_dict()


@router.delete("/resumes/{resume_id}")
def delete_resume(resume_id: int, db: Session = Depends(get_db)):
    row = db.query(Resume).filter(Resume.id == resume_id).first()
    if not row:
        raise HTTPException(404, "Resume not found")
    db.delete(row)
    db.commit()
    return {"ok": True}