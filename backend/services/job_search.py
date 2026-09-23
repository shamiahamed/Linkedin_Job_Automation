"""
Daily job auto-fetch (lands as cards for the user to review/apply manually).

Source: Adzuna India jobs API  (api.adzuna.com/v1/api/jobs/in/search)
  - free tier, real India listings, no login session needed server-side
  - requires ADZUNA_APP_ID / ADZUNA_APP_KEY in env (free registration)
  - search per keyword x city (metropolitan India + all Tamil Nadu cities)

Results are converted to the same JobCreate shape the rest of the app uses
(title/company/location/description/apply_link/salary/source="auto_fetch")
and go through the exact same ingest path as manual captures: dedupe first,
then _auto_apply_if_enabled decides whether they auto-fire or just queue.
"""
import re
import time
import html
import logging
import requests
from config import Config

logger = logging.getLogger("uvicorn.error")

# Indian metropolitan cities (broad coverage beyond Tamil Nadu).
DEFAULT_CITIES = [
    "Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Chennai", "Kolkata",
    "Pune", "Ahmedabad", "Gurugram", "Noida", "Jaipur", "Thane",
    # Tamil Nadu — all major districts/cities
    "Chennai", "Coimbatore", "Madurai", "Tiruchirappalli", "Salem",
    "Tirunelveli", "Erode", "Vellore", "Hosur", "Thanjavur", "Kumbakonam",
    "Karaikudi", "Nagercoil", "Thoothukudi", "Tiruppur", "Cuddalore",
    "Dharmapuri", "Dindigul", "Nagapattinam", "Pudukkottai", "Ramanathapuram",
    "Sivaganga", "Tenkasi", "Viluppuram", "Virudhunagar", "Krishnagiri",
]

# Mainly IT / software, but "all roles" — broad set of searches.
DEFAULT_KEYWORDS = [
    # IT & software (primary)
    "python developer", "java developer", "frontend developer",
    "backend developer", "full stack developer", "software engineer",
    "data analyst", "data engineer", "machine learning", "devops engineer",
    "cloud engineer", "web developer", "react developer", "node.js developer",
    "qa tester", "sql developer", "system administrator", "network engineer",
    "mobile app developer", ".net developer", "ux ui designer",
    "product manager", "project manager", "business analyst",
    "it support", "cyber security", "hr", "sales", "accountant",
    "digital marketing", "content writer", "fresher",
]


def _clean(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", html.unescape(str(text))).strip()


def adzuna_configured() -> bool:
    return bool(Config.ADZUNA_APP_ID and Config.ADZUNA_APP_KEY)


def _salary_text(result: dict) -> str:
    lo, hi = result.get("salary_min"), result.get("salary_max")
    if lo and hi and lo != hi:
        return f"INR {lo:,.0f} - {hi:,.0f} /yr"
    if lo:
        return f"INR {lo:,.0f} /yr"
    return ""


def search_keyword_city(keyword: str, city: str, page: int = 1) -> list:
    """One Adzuna search for a single keyword+city. Returns raw result dicts."""
    if not adzuna_configured():
        return []
    url = f"https://api.adzuna.com/v1/api/jobs/{Config.ADZUNA_COUNTRY}/search/{page}"
    params = {
        "app_id": Config.ADZUNA_APP_ID,
        "app_key": Config.ADZUNA_APP_KEY,
        "what": keyword,
        "where": city,
        "results_per_page": 20,
        "max_days_old": 7,
        "sort_by": "date",
        "content-type": "application/json",
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", []) or []
    except Exception as e:
        logger.error("adzuna search %r/%r failed: %s", keyword, city, e)
        return []


def to_job_dict(result: dict, city: str) -> dict:
    """Map an Adzuna result onto the app's JobCreate shape (auto_fetch source)."""
    comp = ((result.get("company") or {}).get("display_name") or "").strip()
    loc = _clean(result.get("location", {}).get("display_name") or city)
    title = _clean(result.get("title") or "")
    desc = re.sub(r"<[^>]+>", " ", (result.get("description") or ""))
    desc = _clean(desc)[:4000]
    url = (result.get("redirect_url") or "").strip()
    return {
        "title": title,
        "company": comp or "",
        "location": loc,
        "url": url,
        "description": desc,
        "emails": [],
        "phones": [],
        "experience": "",
        "salary": _salary_text(result),
        "source": "auto_fetch",
        # The redirect URL is the apply action — set as apply_link so the job
        # is actionable (status apply_link, not auto-deleted as no_contact).
        "apply_link": url,
    }


def fetch_daily_jobs(keywords=None, cities=None, limit: int = 200, max_seconds: float = 60.0) -> list:
    """Search keyword x city pairs (newest order) until the result limit OR the
    time budget is hit — a run always finishes instead of hanging on slow/empty
    searches. Cities are deduped so metro+TN lists share 'Chennai' only once."""
    keywords = keywords or DEFAULT_KEYWORDS
    cities = list(dict.fromkeys(cities or DEFAULT_CITIES))
    jobs = []
    seen = set()
    if not adzuna_configured():
        return jobs
    deadline = time.monotonic() + max_seconds
    pairs = [(kw, city) for kw in keywords for city in cities]
    for kw, city in pairs:
        if time.monotonic() > deadline:
            break
        for result in search_keyword_city(kw, city):
            j = to_job_dict(result, city)
            if not j["title"]:
                continue
            key = (j["title"].lower(), j["url"], j["company"].lower())
            if key in seen:
                continue
            seen.add(key)
            jobs.append(j)
            if len(jobs) >= limit or time.monotonic() > deadline:
                return jobs
        time.sleep(0.1)
    return jobs