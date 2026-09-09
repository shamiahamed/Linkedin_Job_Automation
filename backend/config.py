import os
import hashlib
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Load .env from backend/ or project root wherever the server is started from
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env")

class Config:
    # Paths
    BASE_DIR = Path(__file__).resolve().parent

    # App
    APP_NAME = "Job Auto-Apply"
    APP_VERSION = "1.0.0"
    DEBUG = os.getenv("DEBUG", "true").lower() == "true"

    # Server
    API_HOST = os.getenv("API_HOST", "0.0.0.0")
    API_PORT = int(os.getenv("API_PORT", "8000"))

    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'job_automation.db'}")
    # Resolve relative sqlite paths against the backend directory so the server
    # and CLI always use the same database regardless of working directory.
    if DATABASE_URL.startswith("sqlite:///./"):
        DATABASE_URL = f"sqlite:///{BASE_DIR / DATABASE_URL.replace('sqlite:///./', '')}"
    # Render/Heroku hand out `postgres://`; SQLAlchemy needs `postgresql://`
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

    # API security (empty token = open local dev; set a strong value when deployed)
    API_TOKEN = os.getenv("API_TOKEN", "")

    # Personal dashboard login (username + password -> session cookie).
    # APP_PASSWORD must be non-empty when deployed; the dashboard uses this login
    # instead of the raw API-token prompt (removes the phishing-page signal).
    APP_USERNAME = os.getenv("APP_USERNAME", "shamim")
    APP_PASSWORD = os.getenv("APP_PASSWORD", "")
    SESSION_SECRET = os.getenv("SESSION_SECRET", "") or hashlib.sha256(
        (APP_USERNAME + ":" + APP_PASSWORD).encode()
    ).hexdigest()

    # Brevo (Email)
    BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
    BREVO_SENDER_EMAIL = os.getenv("EMAIL_FROM", os.getenv("BREVO_SENDER_EMAIL", ""))
    BREVO_SENDER_NAME = os.getenv("EMAIL_FROM_NAME", "Shamim Ahamed J")

    # Groq LLM (optional enrichment — off when key missing)
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

    # Applicant Details
    YOUR_NAME = os.getenv("YOUR_NAME", "Shamim Ahamed J")
    YOUR_PHONE = os.getenv("YOUR_PHONE", "9894593190")
    YOUR_EMAIL = os.getenv("YOUR_EMAIL", "ahamedshamin5@gmail.com")

    # Paths
    RESUMES_DIR = BASE_DIR / "resumes"
    TEMPLATES_DIR = BASE_DIR / "templates"
    UPLOADS_DIR = BASE_DIR / "uploads"

    # Ensure directories
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
