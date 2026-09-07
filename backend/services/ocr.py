"""
OCR processing using Tesseract.
Install Tesseract on Windows: https://github.com/UB-Mannheim/tesseract/wiki
"""
import subprocess
import sys
import pytesseract
from PIL import Image
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

    def image_to_text(self, image_path: str) -> str:
        """Extract text from an image file."""
        img = Image.open(image_path)
        # Upscale small images for better OCR
        if img.width < 800:
            img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
        img = img.convert("L")  # Grayscale
        text = pytesseract.image_to_string(img)
        return text.strip()

    def process_screenshot(self, image_path: str) -> dict:
        """Full pipeline: OCR -> parse -> structured job data."""
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
        parsed = JobParser.parse(text)
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