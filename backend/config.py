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

    # Gmail API (the only mailer now — works on Render free tier via HTTPS 443)
    GMAIL_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID", "")
    GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET", "")
    GMAIL_REFRESH_TOKEN = os.getenv("GMAIL_REFRESH_TOKEN", "")
    GMAIL_USER = os.getenv("GMAIL_USER", os.getenv("EMAIL_FROM", "ahamedshamin5@gmail.com"))
    EMAIL_FROM = os.getenv("EMAIL_FROM", "ahamedshamin5@gmail.com")
    EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Shamim Ahamed J")

    # Groq LLM (optional enrichment — off when key missing)
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

    # Adzuna India jobs API (free; used by the daily auto-fetch. off when keys missing)
    ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
    ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
    ADZUNA_COUNTRY = os.getenv("ADZUNA_COUNTRY", "in")
    # Only "recently posted" listings (Adzuna trailing-days filter) so fetched
    # cards are fresh/real openings, not stale reposts.
    ADZUNA_MAX_DAYS_OLD = int(os.getenv("ADZUNA_MAX_DAYS_OLD", "3") or "3")

    # Google RSS job source (second external feed; deterministic parse, no key).
    # Set GOOGLE_RSS_ENABLED=0 to turn this source off entirely.
    GOOGLE_RSS_ENABLED = os.getenv("GOOGLE_RSS_ENABLED", "true")
    # Direct feed URLs (comma-separated) — when set, these are used as-is and
    # GOOGLE_RSS_SEARCHES is ignored. URL values are NOT rewritten.
    GOOGLE_RSS_FEED_URLS = os.getenv("GOOGLE_RSS_FEED_URLS", "")
    # Query/location pairs ("query-A|location-A,query-B|location-B"); falls back
    # to the built-in DEFAULT_SEARCHES when empty.
    GOOGLE_RSS_SEARCHES = os.getenv("GOOGLE_RSS_SEARCHES", "")
    # URL template with {query} / {location} placeholders (both URL-quoted).
    GOOGLE_RSS_URL_TEMPLATE = os.getenv(
        "GOOGLE_RSS_URL_TEMPLATE",
        "https://www.google.com/search?q={query}+{location}+jobs&output=rss&num=20",
    )

    # Job Analysis Agent (per-capture enrichment; additive — never overrides the
    # auto-apply/email/notify decisions, never deletes a job). OFF by default so
    # existing behaviour is 100% unchanged until the user turns it on.
    JOB_ANALYSIS_ENABLED = os.getenv("JOB_ANALYSIS_ENABLED", "false")
    # Use the Groq LLM to enrich fields the deterministic core could not decide.
    # Off (or no GROQ_API_KEY) = deterministic core only.
    JOB_ANALYSIS_USE_LLM = os.getenv("JOB_ANALYSIS_USE_LLM", "true")

    # Applicant profile (Job Analysis Agent). Central place the analyzer matches
    # against; comma-separated values, env-overridable. Roles are matched as
    # substrings against the job title (lower-cased); skills against the full
    # title+description text; locations are the metro/TN places the user works in
    # (plus "Remote"); MAX_EXPERIENCE_YEARS caps roles the user can attend.
    PROFILE_ROLES = os.getenv("PROFILE_ROLES", "")
    PROFILE_SKILLS = os.getenv("PROFILE_SKILLS", "")
    PROFILE_LOCATIONS = os.getenv("PROFILE_LOCATIONS", "")
    PROFILE_MAX_EXPERIENCE_YEARS = os.getenv("PROFILE_MAX_EXPERIENCE_YEARS", "2")

    # Job Intelligence Agent (per-capture evaluation; additive enrichment ONLY —
    # never sends emails/notifications, never auto-applies, never changes status,
    # dedupe, retention, or any downstream automation). OFF by default so existing
    # behaviour is 100% unchanged until the user turns it on. It evaluates each
    # new job against the existing deterministic Job Analysis results, so enable
    # JOB_ANALYSIS_ENABLED too (its signals are the agent's authority).
    JOB_INTELLIGENCE_ENABLED = os.getenv("JOB_INTELLIGENCE_ENABLED", "false")
    # Use Groq to enrich ONLY the narrative (missing skills / summary / extra
    # concerns). The deterministic verdict — the booleans and the match score —
    # is always computed locally and can never be overridden by the LLM.
    # Off (or no GROQ_API_KEY) = deterministic evaluation only.
    JOB_INTELLIGENCE_USE_LLM = os.getenv("JOB_INTELLIGENCE_USE_LLM", "true")
    # Per-evaluation Groq timeout (seconds). Bounded; failures degrade to a safe
    # "unavailable"/deterministic-only result and never break job ingestion.
    JOB_INTELLIGENCE_TIMEOUT = int(os.getenv("JOB_INTELLIGENCE_TIMEOUT", "45") or "45")

    # Temporal (Phase 3) — scheduled Google RSS + Adzuna fetch took over by a
    # dedicated Temporal worker service (backend/temporal_worker.py). When
    # TEMPORAL_ADDRESS is empty the worker does NOT start and the built-in
    # main.py scheduler stays in charge, so the app works with or without
    # Temporal. Point both the web AND worker services at Temporal so the web
    # service also hands over the daily fetch (no double-fetching).
    TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "")
    TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
    # Temporal Cloud API key (mTLS); TLS is implied when the key is set.
    TEMPORAL_API_KEY = os.getenv("TEMPORAL_API_KEY", "")
    TEMPORAL_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "job-auto-apply")
    # How often the Temporal "every 12h" fetch schedule fires.
    TEMPORAL_FETCH_INTERVAL_HOURS = int(os.getenv("TEMPORAL_FETCH_INTERVAL_HOURS", "12") or "12")

    # Applicant Details
    YOUR_NAME = os.getenv("YOUR_NAME", "Shamim Ahamed J")
    YOUR_PHONE = os.getenv("YOUR_PHONE", "9894593190")
    YOUR_EMAIL = os.getenv("YOUR_EMAIL", "ahamedshamin5@gmail.com")
    # Constant hands-on experience assumed in every application email (e.g. "1").
    YOUR_EXPERIENCE_YEARS = os.getenv("YOUR_EXPERIENCE_YEARS", "1")

    # Paths
    RESUMES_DIR = BASE_DIR / "resumes"
    TEMPLATES_DIR = BASE_DIR / "templates"
    UPLOADS_DIR = BASE_DIR / "uploads"

    # Ensure directories
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
