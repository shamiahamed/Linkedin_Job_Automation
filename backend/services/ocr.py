"""
OCR processing using Tesseract.
Install Tesseract on Windows: https://github.com/UB-Mannheim/tesseract/wiki
"""
import subprocess
import sys
import time
import pytesseract
from PIL import Image, ImageOps
from pathlib import Path
from config import Config

# Common Windows install locations
TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


def _find_tesseract() -> str:
    for path in TESSERACT_PATHS:
        if Path(path).exists():
            return path
    # Fall back to PATH
    return "tesseract"


class OCRProcessor:
    def __init__(self):
        pytesseract.pytesseract.tesseract_cmd = _find_tesseract()

    @staticmethod
    def _ocr(variant, psm: str, timeout: int = 18) -> str:
        """Wrapped tesseract call — never raises, never blocks past `timeout`."""
        try:
            txt = pytesseract.image_to_string(
                variant, config=f"--oem 1 --psm {psm}", timeout=timeout
            )
            return (txt or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _score(text: str) -> int:
        """Number of real word-like tokens — ignores punctuation/noise lines."""
        if not text:
            return 0
        import re
        return sum(
            1
            for w in text.split()
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9.,&'’\-/\+]{1,}", w)
        )

    def image_to_text(self, image_path: str) -> str:
        """Extract text quickly and surely from an image file.

        Fast fixed-cost preprocessing keeps Tesseract inside Render's free-tier
        response window: grayscale -> autocontrast, cap oversized images, and a
        tiny 2x upscale for very small ones. Several cheap PSM passes with hard
        timeouts, best-scoring result wins (stops early when clearly good).
        Returns the best text or '' — never hangs, never 502s.
        """
        img = Image.open(image_path)
        img = img.convert("L")
        w, h = img.size
        max_dim = 800
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        elif w < 700:
            img = img.resize((w * 2, h * 2), Image.LANCZOS)
        img = ImageOps.autocontrast(img)
        binary = img.point(lambda p: 255 if p > 140 else 0)

        # Hard budget: stays well under Render's 60s response cap on 0.1 CPU.
        passes = [
            (img, "6", 15),
            (binary, "11", 15),
            (img, "11", 12),
        ]
        best, best_score, got_good, started = "", 0, False, time.time()
        for variant, psm, to in passes:
            if time.time() - started > 38:
                break
            txt = self._ocr(variant, psm, timeout=to)
            score = self._score(txt)
            if score > best_score:
                best, best_score = txt, score
            if score >= 6:
                got_good = True
                break
        if got_good or best_score >= 4:
            return best
        if best:
            return best
        # No readable text at all
        raise ValueError(
            "Could not extract any readable text from this image. "
            "Use a sharper/brighter screenshot (crop out dark backgrounds)."
        )

    def process_screenshot(self, image_path: str) -> dict:
        """Full pipeline: OCR -> parse (LLM-refined when available) -> job data."""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        # Save to uploads if it's not already there
        dest = Config.UPLOADS_DIR / path.name
        if path != dest and not dest.exists():
            import shutil
            shutil.copy2(path, dest)

        text = self.image_to_text(str(dest))

        from services.job_parser import JobParser
        from services.llm import extract_job

        parsed = JobParser.parse(text)
        llm = extract_job(text) if text.strip() else {}
        if llm:
            for key in ("title", "company", "location", "experience", "salary", "apply_link"):
                if llm.get(key):
                    parsed[key] = llm[key]
            if llm.get("emails"):
                parsed["emails"] = llm["emails"]
            if llm.get("phones"):
                parsed["phones"] = llm["phones"]
            parsed["has_email"] = bool(parsed.get("emails"))
            parsed["has_phone"] = bool(parsed.get("phones"))

        parsed["source"] = "ocr"
        parsed["image_path"] = str(dest)
        return parsed


def check_tesseract() -> bool:
    """Check if Tesseract is installed and usable."""
    try:
        subprocess.run(
            [_find_tesseract(), "--version"],
            capture_output=True,
            timeout=5,
        )
        return True
    except Exception:
        return False