from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import re
import threading
from database import get_db
from models import Job, Application, Setting, Resume
from services.email_builder import EmailBuilder


router = APIRouter(prefix="/api", tags=["jobs"])

# Serializes job capture so two same-second feed posts can't both pass the
# content-dedupe check (each inserts before the other is committed).
_CREATE_LOCK = threading.Lock()


class JobCreate(BaseModel):
    title: str
    company: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    emails: List[str] = []
    phones: List[str] = []
    experience: Optional[str] = None
    salary: Optional[str] = None
    source: Optional[str] = "linkedin"
    apply_link: Optional[str] = ""


class ApplyResponse(BaseModel):
    success: bool
    type: Optional[str] = None
    to_email: Optional[str] = None
    resume_used: Optional[str] = None
    error: Optional[str] = None
    duplicate_email: Optional[bool] = None


class JobStatusUpdate(BaseModel):
    status: str


@router.post("/jobs")
def create_job(job_data: JobCreate, db: Session = Depends(get_db)):
    with _CREATE_LOCK:
        # Optional Groq refinement for weak captures (generic title, or a post with
        # no email/phone/link — typically image posts). Errors are swallowed, so the
        # heuristic pipeline below still runs when the LLM is off/unreachable.
        if job_data.description:
            weak_title = _title_weak(job_data.title)
            weak_contact = not job_data.emails and not job_data.phones and not (job_data.apply_link or "")
            if weak_title or weak_contact:
                try:
                    from services import llm as _llm

                    ref = _llm.extract_job(job_data.description)
                    if ref:
                        emails = sorted(set((job_data.emails or []) + (ref.get("emails") or [])))
                        phones = sorted(set((job_data.phones or []) + (ref.get("phones") or [])))
                        # Replace empty OR clearly-junk company ("Face" from
                        # "Face to Face interview", person names) with LLM's find.
                        fixed_company = job_data.company
                        if not fixed_company or not _company_ok(fixed_company):
                            fixed_company = ref.get("company") or fixed_company
                        job_data = JobCreate(
                            title=(ref.get("title") or job_data.title) if weak_title else job_data.title,
                            company=fixed_company,
                            location=(ref.get("location") or job_data.location) if not job_data.location else job_data.location,
                            url=job_data.url,
                            description=job_data.description,
                            emails=emails,
                            phones=phones,
                            experience=(ref.get("experience") or job_data.experience) if not job_data.experience else job_data.experience,
                            salary=(ref.get("salary") or job_data.salary) if not job_data.salary else job_data.salary,
                            source=job_data.source or "linkedin",
                            apply_link=(ref.get("apply_link") or job_data.apply_link or ""),
                        )
                except Exception:
                    pass

        existing = db.query(Job).filter(Job.url == job_data.url).first() if job_data.url else None
        if existing:
            return existing.to_dict()

        # Feed posts have no URL, so dedupe on content instead (title/company/location/emails).
        # Normalize case and whitespace so the same post re-captured always matches.
        def _norm(s):
            return " ".join((s or "").lower().split())

        peers = db.query(Job).filter(
            func.lower(func.replace(Job.title, ' ', '')) == _norm(job_data.title).replace(' ', ''),
            func.lower(func.replace(func.coalesce(Job.company, ''), ' ', '')) == _norm(job_data.company).replace(' ', ''),
            func.lower(func.replace(func.coalesce(Job.location, ''), ' ', '')) == _norm(job_data.location).replace(' ', ''),
        ).all()
        for d in peers:
            if sorted(d.emails or []) == sorted(job_data.emails):
                return d.to_dict()

        experience = job_data.experience
        if not experience and job_data.description and FRESHER_RE.search(job_data.description.lower()):
            experience = "Fresher"

        job = Job(
            title=job_data.title,
            company=job_data.company,
            location=job_data.location,
            url=job_data.url,
            description=job_data.description,
            emails=job_data.emails,
            phones=job_data.phones,
            experience=experience,
            salary=job_data.salary,
            source=job_data.source or "linkedin",
            apply_link=job_data.apply_link or "",
            has_email=len(job_data.emails) > 0,
            has_phone=len(job_data.phones) > 0,
        )
        db.add(job)
        try:
            db.commit()
            db.refresh(job)
        except Exception:
            db.rollback()
            raise

        # No email/phone/link -> mark for auto-cleanup (deleted after 24h by cleanup_no_contact).
        # A post with an apply link is NOT dead: it's actionable via the link.
        if not job.emails and not job.phones:
            job.status = "apply_link" if job.apply_link else "no_contact"

        _auto_apply_if_enabled(db, job)
        db.commit()
        db.refresh(job)
        return job.to_dict()


class TextCapture(BaseModel):
    text: str
    source: Optional[str] = "mobile"


@router.post("/jobs/from-text")
def create_job_from_text(payload: TextCapture, db: Session = Depends(get_db)):
    """Mobile/paste capture: raw LinkedIn post text -> LLM extraction -> the same
    create/dedupe/gate/auto-apply flow the extension uses."""
    text = (payload.text or "").strip()
    if len(text) < 30:
        raise HTTPException(400, "Pasted text is too short to be a job post")

    from services import llm as _llm

    ref = _llm.extract_job(text)
    if not ref or not ref.get("title"):
        raise HTTPException(
            422,
            "Could not recognize a job in this text. Paste more of the post "
            "(include the role, company and any contact info).",
        )
    return create_job(
        JobCreate(
            title=ref["title"],
            company=ref.get("company"),
            location=ref.get("location"),
            url=None,
            description=text,
            emails=ref.get("emails") or [],
            phones=ref.get("phones") or [],
            experience=ref.get("experience"),
            salary=ref.get("salary"),
            source=payload.source or "mobile",
            apply_link=ref.get("apply_link") or "",
        ),
        db,
    )


EXPERIENCE_BUCKETS = {"0-1": (0, 12), "1-3": (13, 36), "3+": (37, None)}


@router.get("/jobs")
def list_jobs(
    status: Optional[str] = None,
    exp: Optional[str] = None,
    source: Optional[str] = None,
    q: Optional[str] = None,
    since: Optional[str] = None,
    db: Session = Depends(get_db),
):
    cleanup_no_contact(db)
    query = db.query(Job).order_by(Job.created_at.desc())
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            query = query.filter(Job.status.in_(statuses))
    if source:
        query = query.filter(Job.source == source)
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            query = query.filter(Job.created_at >= since_dt)
        except ValueError:
            pass
    jobs = query.all()
    if q:
        ql = q.lower()
        jobs = [j for j in jobs if ql in (j.title or "").lower() or ql in (j.company or "").lower()]
    if exp and exp != "any":
        lo, hi = EXPERIENCE_BUCKETS.get(exp, (None, None))
        if lo is not None or hi is not None:
            jobs = [
                j for j in jobs
                if (lambda m: m is not None
                    and (lo is None or m >= lo)
                    and (hi is None or m <= hi))(
                    _exp_min_months(f"{j.experience or ''} {_feed_body(j.description or '')}"))
            ]
    return [j.to_dict() for j in jobs]


def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else default


def _has_sent(db: Session, job_id: int, type_: str) -> bool:
    """True if a successful application of this type already went out for this job."""
    return db.query(Application).filter(
        Application.job_id == job_id,
        Application.type == type_,
        Application.status == "sent",
    ).first() is not None


def _email_previously_sent(db: Session, email: str) -> bool:
    """True if an application email already went out to this address (any job).
    Used to avoid auto-emailing the same recruiter/company twice — that post is
    left as 'duplicate' for the user to verify manually before re-applying."""
    if not email:
        return False
    return db.query(Application).filter(
        Application.email_sent_to == email,
        Application.type == "email",
        Application.status == "sent",
    ).first() is not None


def _feed_body(text: str) -> str:
    """Strip LinkedIn's feed actor/header block ('Feed post <actor> <time> Follow …')
    and the trailing '… more' so poster profile headlines (e.g. '10+ Years in
    Automotive') never leak into experience parsing. Mirrors feedTitleText()."""
    t = re.sub(r"^Feed post\s*", "", text or "", flags=re.I)
    t = re.sub(r"\s*(?:…|\.\.\.)\s*[Mm]ore\b.*$", "", t, flags=re.S)
    for _ in range(2):
        t = re.sub(r"^.{0,260}?\b\d+[smhdw] ?(?:Follow(?:ing)?\s*)?", "", t, flags=re.S)
    t = re.sub(r"^.{0,260}?\bFollow(?:ing)?\s*", "", t, flags=re.I)
    return t


def _exp_months(text: str) -> Optional[int]:
    """Max experience implied by the text in months; None when not explicitly stated.
    Fresher/entry-level/recent-grad mentions count as 12 (<=1 year)."""
    t = " " + " ".join((text or "").lower().split()) + " "
    months = None
    if FRESHER_RE.search(t):
        months = 12
    for m in re.finditer(
        r"\b(\d{1,2})(?:\s*(?:[-–—to+]\s*)(\d{1,2}))?\s*(?:plus|\+)?\s*(?:years?|yrs?)\b", t
    ):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        months = max(months, max(a, b) * 12) if months is not None else max(a, b) * 12
    return months


def _exp_min_months(text: str) -> Optional[int]:
    """Lowest experience mentioned (start of a range like its lower bound),
    in months; None when not explicitly stated. Fresher/entry-level/recent-grad
    count as 12 (<=1 year). Used to decide auto-apply eligibility: only roles
    starting at <=1 year (0-1, 1, 1-2, 1-3, fresher) are auto-applied."""
    t = " " + " ".join((text or "").lower().split()) + " "
    mins = []
    if FRESHER_RE.search(t):
        mins.append(12)
    for m in re.finditer(
        r"\b(\d{1,2})(?:\s*(?:[-–—]|to)\s*(\d{1,2}))?\s*(?:plus|\+)?\s*(?:years?|yrs?)\b", t
    ):
        mins.append(int(m.group(1)) * 12)
    return min(mins) if mins else None


FRESHER_RE = re.compile(r"\b(freshers?|entry[- ]?level|0 experience|no experience|recent graduates?|graduate trainee|passouts?)\b")

_TITLE_JUNK_RE = re.compile(r"^(?:job opening|open position|vacancy)\b", re.I)


def _title_weak(title) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    if _TITLE_JUNK_RE.match(t):
        return True
    if any(ch in t for ch in "🎓💼📌⚡📍"):
        return True
    low = t.lower()
    if "eligibility" in low or "experience:" in low:
        return True
    return False
_COMPANY_STOP = {"the", "a", "an", "our", "we", "you", "your", "hiring", "job", "fresher", "candidate", "inc", "ltd"}
_COMPANY_LEGAL = {"pvt", "ltd", "llp", "llc", "inc", "private", "limited", "and", "&", "&amp;"}


def _company_ok(company: str) -> bool:
    """A confident company name: real title-cased name, not a sentence fragment
    ('American businesses,' / person names / 4-letter filenames like 'Face' /
    'that MitrahSoft' / 'HODs can secure')."""
    c = (company or "").strip()
    words = [w for w in c.split() if w]
    if not words or len(c) < 5 or not re.search(r"[A-Z]", c):
        return False
    first = words[0].lower().rstrip(".,")
    # A real company rarely starts with a pronoun/verb filler mid-sentence.
    if first in _COMPANY_STOP or first in {"that", "we", "they", "i", "me", "you",
                                          "here", "now", "join", "started", "new"}:
        return False
    for w in words[1:]:
        if re.match(r"^[a-z]", w) and w.rstrip(".,").lower() not in _COMPANY_LEGAL:
            return False
    return True


def _confident(job) -> bool:
    """True when the captured title + company are solid enough to auto-email a
    recruiter. Generic/junk captures ('Job Opening', 'Face', a person's name)
    produce an unusable email body and get the profile rejected — those must
    stay 'pending' for manual review instead of going out automatically."""
    title = (job.title or "").strip()
    company = (job.company or "").strip()
    if not title or len(title) < 4:
        return False
    if _TITLE_JUNK_RE.match(title):
        return False
    low = title.lower()
    if "eligibility" in low or "experience:" in low or re.search(r"[🎓💼📌⚡📍]", title):
        return False
    if not _company_ok(company):
        return False
    if company.lower() in _COMPANY_STOP:
        return False
    return True


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def _auto_apply_if_enabled(db: Session, job: Job) -> None:
    """On capture (when auto-apply is on):
    - email present -> application emailed automatically ONLY when the role
      starts at <=1 year experience (0-1, 1, 1-2, 1-3, fresher). Roles like
      2+ / 3-5 years stay 'pending' for the user to apply manually.
    - confirm_before_send ON -> confident email jobs are staged as
      'ready_to_send' and never emailed until the user presses Confirm.
    - phone present -> call-summary emailed to the user's own inbox so they can call
    - apply link only -> the apply link is emailed to the user's own inbox so the
      user opens and APPLIES it themselves (no auto-open of browser tabs)"""
    if _get_setting(db, "auto_apply", "1") not in ("1", "true", "yes"):
        return
    confirm = _get_setting(db, "confirm_before_send", "0") in ("1", "true", "yes")
    # Safety: a junk title/company capture must NOT get an auto-email (empty or
    # wrong body = instant rejection). Unconfident posts stay pending for review.
    try:
        # 1. Phone job -> call-summary to the user's own inbox (safe, always auto).
        if job.phones and not _has_sent(db, job.id, "phone_summary"):
            summary = EmailBuilder(job).send_phone_summary()
            if summary.get("success"):
                db.add(Application(
                    job_id=job.id,
                    email_sent_to=summary.get("to_email"),
                    email_response=summary.get("message_id"),
                    type="phone_summary",
                    status="sent",
                ))
                # Phone-only job whose summary already went out -> reflect it.
                if not job.emails and not job.apply_link and job.status in (None, "", "pending"):
                    job.status = "phone_summary_sent"
        if not _confident(job):
            if job.emails and job.status in (None, "", "pending"):
                job.status = "pending"
            return

        min_months = _exp_min_months(f"{job.experience or ''} {_feed_body(job.description or '')}")
        eligible = bool(job.emails) and min_months is not None and min_months <= 12

        if confirm and job.emails and not _has_sent(db, job.id, "email"):
            # Confirm-before-send: stage it for the dashboard, don't email yet.
            job.status = "ready_to_send"
            return
        if job.emails and eligible and not _has_sent(db, job.id, "email"):
            if _email_previously_sent(db, job.emails[0]):
                # Same recruiter/company email already got an application -> manual verify only.
                job.status = "duplicate"
            else:
                result = EmailBuilder(job).send_application()
                if result.get("success"):
                    job.status = "applied"
                    db.add(Application(
                        job_id=job.id,
                        resume_used=result.get("resume_used"),
                        email_sent_to=result.get("to_email"),
                        email_response=result.get("message_id"),
                        type="email",
                        status="sent",
                    ))
                elif not job.phones:
                    job.status = "pending"
        elif not job.emails and job.apply_link and not _has_sent(db, job.id, "link_summary"):
            # Link-only job -> email the apply link to the user's own inbox, they apply manually.
            summary = EmailBuilder(job).send_link_summary()
            if summary.get("success"):
                db.add(Application(
                    job_id=job.id,
                    email_sent_to=summary.get("to_email"),
                    email_response=summary.get("message_id"),
                    type="link_summary",
                    status="sent",
                ))
                job.status = "link_email_sent"
    except Exception:
        job.status = "pending"


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return {
        "auto_apply": _get_setting(db, "auto_apply", "1") == "1",
        "confirm_before_send": _get_setting(db, "confirm_before_send", "0") in ("1", "true", "yes"),
    }


@router.put("/settings")
def update_settings(payload: dict, db: Session = Depends(get_db)):
    _set_setting(db, "auto_apply", "1" if payload.get("auto_apply") else "0")
    if payload.get("confirm_before_send") is not None:
        _set_setting(db, "confirm_before_send", "1" if payload.get("confirm_before_send") else "0")
    db.commit()
    return get_settings(db)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


def _resolve_resume_override(db: Session, resume_id) -> Optional[dict]:
    """Turn an uploaded-resume id into the attachment payload {name, data(base64)}
    the EmailBuilder can attach. None -> auto-select from the resumes/ folder."""
    if not resume_id:
        return None
    row = db.query(Resume).filter(Resume.id == int(resume_id)).first()
    return {"name": row.name, "data": row.data} if row else None


@router.post("/jobs/{job_id}/apply", response_model=ApplyResponse)
def apply_to_job(job_id: int, payload: dict = Body(default=None), db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    payload = payload or {}

    try:
        resume_override = _resolve_resume_override(db, payload.get("resume_id"))
        builder = EmailBuilder(job, resume_override=resume_override)

        if job.emails:
            # Manual apply = deliberate; allow re-send as a follow-up. But if this
            # contact email already received an application (any job), stop unless
            # the user confirms on the dashboard — catches double-clicks/dup rows.
            if _email_previously_sent(db, job.emails[0]) and not payload.get("confirm_duplicate"):
                return {
                    "success": False,
                    "duplicate_email": True,
                    "error": "This contact email has already received an application. Confirm to send anyway.",
                }
            result = builder.send_application()
            if result.get("success"):
                job.status = "applied"
                db.add(Application(
                    job_id=job.id,
                    resume_used=result.get("resume_used"),
                    email_sent_to=result.get("to_email"),
                    email_response=result.get("message_id"),
                    type="email",
                    status="sent",
                ))
                db.commit()
                return result
            return result

        # Phone-only: send summary to the applicant's own email (only if a phone exists)
        if not job.phones:
            return {"success": False, "error": "No email or phone on this post to contact."}
        result = builder.send_phone_summary()
        if result.get("success"):
            job.status = "phone_summary_sent"
            db.add(Application(
                job_id=job.id,
                resume_used=None,
                email_sent_to=None,
                email_response=result.get("message_id"),
                type="phone_summary",
                status="sent",
            ))
            db.commit()
            return result
        return result
    except Exception as e:
        db.rollback()
        return {"success": False, "error": f"Apply failed: {type(e).__name__}: {str(e)}"}


@router.post("/jobs/{job_id}/confirm", response_model=ApplyResponse)
def confirm_and_send(job_id: int, payload: dict = Body(default=None), db: Session = Depends(get_db)):
    """User pressed 'Confirm' on the dashboard for a staged 'ready_to_send' job.
    Sends the application email (optionally with a specific uploaded resume and
    a manual resume-pin) and marks the job applied."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.emails:
        raise HTTPException(400, "This job has no email contact to confirm.")
    payload = payload or {}
    try:
        resume_override = _resolve_resume_override(db, payload.get("resume_id"))
        resume_pin = payload.get("resume_pin")
        builder = EmailBuilder(job, resume_override=resume_override, resume_pin=resume_pin)
        if _email_previously_sent(db, job.emails[0]) and not payload.get("confirm_duplicate"):
            return {
                "success": False,
                "duplicate_email": True,
                "error": "This contact email already received an application. Confirm to send anyway.",
            }
        result = builder.send_application()
        if result.get("success"):
            job.status = "applied"
            db.add(Application(
                job_id=job.id,
                resume_used=result.get("resume_used"),
                email_sent_to=result.get("to_email"),
                email_response=result.get("message_id"),
                type="email",
                status="sent",
            ))
            db.commit()
            return result
        return result
    except Exception as e:
        db.rollback()
        return {"success": False, "error": f"Apply failed: {type(e).__name__}: {str(e)}"}


@router.patch("/jobs/{job_id}")
def update_job_status(job_id: int, update: JobStatusUpdate, db: Session = Depends(get_db)):
    """Move a job between statuses (e.g. reject a staged 'ready_to_send' back to
    'pending', or restore a duplicate to 'pending')."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    job.status = update.status
    db.commit()
    db.refresh(job)
    return job.to_dict()


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")
    db.query(Application).filter(Application.job_id == job_id).delete()
    db.delete(job)
    db.commit()
    return {"success": True, "deleted_id": job_id}


def cleanup_no_contact(db: Session, max_age_hours: int = 24) -> int:
    """Delete no-contact jobs older than max_age_hours (keeps the table self-cleaning).
    Jobs carrying an apply link are actionable and are never auto-deleted."""
    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
    rows = db.query(Job).filter(
        Job.status == "no_contact",
        Job.created_at < cutoff,
        or_(Job.apply_link.is_(None), Job.apply_link == ""),
    ).all()
    count = 0
    for j in rows:
        db.query(Application).filter(Application.job_id == j.id).delete()
        db.delete(j)
        count += 1
    if count:
        db.commit()
    return count