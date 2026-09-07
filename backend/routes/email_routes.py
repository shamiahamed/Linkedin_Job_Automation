from fastapi import APIRouter, Body, Depends, File, UploadFile, HTTPException
from sqlalchemy.orm import Session
from pathlib import Path
import os
import time
import tempfile
import requests as http_requests
from database import get_db
from models import Job
from services.ocr import OCRProcessor
from services.email_sender import EmailSender


router = APIRouter(prefix="/api", tags=["OCR & Monitor"])
ocr = OCRProcessor()

# URL -> (text, ts) cache so the same post image is never OCR'd twice in a short window.
_IMG_CACHE: dict = {}


@router.post("/ocr/upload")
async def ocr_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a screenshot image, run OCR, parse job data, and store the job."""
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")

    from config import Config
    dest = Config.UPLOADS_DIR / file.filename
    with open(dest, "wb") as f:
        f.write(await file.read())

    try:
        parsed = ocr.process_screenshot(str(dest))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"OCR failed: {e}")

    if not parsed.get("title"):
        return {
            "success": False,
            "parsed": parsed,
            "message": "Could not confidently identify a job title from this image. Review parsed data below.",
        }

    job = Job(
        title=parsed["title"],
        company=parsed.get("company"),
        location=parsed.get("location"),
        description=parsed.get("description"),
        emails=parsed.get("emails", []),
        phones=parsed.get("phones", []),
        experience=parsed.get("experience", ""),
        salary=parsed.get("salary", ""),
        source="ocr",
        has_email=parsed.get("has_email", False),
        has_phone=parsed.get("has_phone", False),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    return {
        "success": True,
        "job": job.to_dict(),
        "message": "Job captured from screenshot via OCR",
    }


@router.get("/ocr/status")
def ocr_status():
    """Check if Tesseract is installed."""
    from services.ocr import check_tesseract
    return {"tesseract_installed": check_tesseract()}


@router.post("/ocr/from-image")
def ocr_from_image(payload: dict = Body(default={})):
    """OCR a job-post image directly from its URL (LinkedIn /dms/ images, etc.).
    Used by the extension when a feed post's contact details live inside an image."""
    url = (payload or {}).get("url", "").strip()
    if not url or len(url) > 4096 or not url.startswith(("https://", "http://")):
        raise HTTPException(400, "invalid url")

    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith((".local", ".internal")):
        raise HTTPException(400, "url not allowed")

    cached = _IMG_CACHE.get(url)
    if cached and time.time() - cached[1] < 3600:
        return {"success": bool(cached[0]), "text": cached[0], "cached": True}

    try:
        resp = http_requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        resp.raise_for_status()
        if not (resp.headers.get("Content-Type") or "").startswith("image/"):
            raise HTTPException(400, "response is not an image")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"image fetch failed: {e}")

    from services.ocr import check_tesseract

    if not check_tesseract():
        raise HTTPException(503, "Tesseract is not installed")

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    try:
        tmp.write(resp.content)
        tmp.close()
        text = ocr.image_to_text(tmp.name)
    finally:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    _IMG_CACHE[url] = (text, time.time())
    return {"success": bool(text.strip()), "text": text.strip(), "cached": False}


@router.post("/email/test")
def test_email():
    """Send a test email to verify Brevo credentials."""
    sender = EmailSender()
    if not sender.configured:
        return {
            "success": False,
            "error": "Brevo API key not set. Add BREVO_API_KEY to .env",
        }
    from config import Config
    result = sender.send_email(
        to_email=Config.YOUR_EMAIL,
        subject="✅ Test Email - Job Auto-Apply",
        html_content="<h3>Test successful!</h3><p>Your Brevo configuration works.</p>",
        to_name=Config.YOUR_NAME,
    )
    return result


@router.get("/health")
def health():
    return {"status": "ok", "app": "Job Auto-Apply"}