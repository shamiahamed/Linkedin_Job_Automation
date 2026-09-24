"""Job Analysis Agent — deterministic core + optional LLM enrichment.

Smallest-safe, additive enrichment that runs when Config.JOB_ANALYSIS_ENABLED is
truthy. It NEVER changes behaviour: it does not auto-apply, email, dedupe,
delete, or notify — it only produces a `job_analysis` JSON document stored on
the Job row (see models.Job.job_analysis) and logged as
JOB_ANALYSIS_STARTED/COMPLETED/FAILED.

Design:
  - The deterministic regex "core" is AUTHORITATIVE for every field it can
    decide (role, experience, seniority, location, walk-in). The LLM (reused
    from services.llm.analyze_job) is only consulted for fields the core left
    unknown, and its output is type-validated before use — so a confident core
    match/no-match is never overridden by LLM noise.
  - analyze_job() NEVER raises: any unexpected failure is caught and turned
    into {"analysis_status": "failed"} so the ingest pipeline always survives.
  - Logs never include the job description or any secrets.

Output schema (stored as JSON on the job):
  {
    "analysis_status": "ok" | "failed",
    "analyzed_at": ISO-8601 UTC string,
    "llm_enriched": bool,
    "role":        {"matched": bool|None, "matched_role": str|None, "reason": str|None},
    "experience":  {"matched": bool|None, "min_years": float|None,
                    "max_years": float|None, "required_text": str|None, "reason": str|None},
    "skills":      {"matched": bool|None, "matched_skills": [str],
                    "missing_skills": [str], "reason": str|None},
    "location":    {"matched": bool|None, "type": "remote"|"hybrid"|"on-site"|"unknown",
                    "reason": str|None},
    "seniority":   {"matched": bool|None, "level": "entry"|"mid"|"senior"|"leadership"|"unknown",
                    "reason": str|None},
    "walk_in":     {"is_walk_in": bool, "date": str|None, "time": str|None, "venue": str|None},
    "overall_match": {"is_match": bool|None, "confidence": "high"|"medium"|"low",
                      "summary": str|None}
  }
"""
import re
import logging
from datetime import datetime, timezone

from config import Config
from services import user_profile

logger = logging.getLogger("uvicorn.error")

_YEARS_RE = re.compile(
    r"(?:\b(?:experience|exp\.?|years? of (?:experience|exp)|freshers?\s*(?:can|with)?)"
    r"[^.]{0,40}?)?"
    r"\b(\d{1,2}(?:\.\d)?)\s*(?:[-–—to]\s*(\d{1,2}(?:\.\d)?))?\s*(?:plus|\+)?"
    r"\s*(?:years?|yrs?)\b",
    re.I,
)
_FRESHER_RE = re.compile(
    r"\b(freshers?|entry[- ]?level|0\s+(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp\.?)|"
    r"no\s+experience|recent\s+graduates?|graduate\s+trainee|passouts?|"
    r"0\s*[-–]\s*1\s*(?:years?|yrs?))\b",
    re.I,
)
_SENIOR_RE = re.compile(
    r"\b(senior|sr\.?|principal|staff|lead\b|manager|director|architect)\b", re.I
)
_LEADERSHIP_RE = re.compile(
    r"\b(director|vice\s+president|\bvp\b|head\s+of|chief(?:\s+\w+){0,2}\s+officer|\bcto\b|"
    r"\bcfo\b|\bceo\b|managing\s+director)\b",
    re.I,
)
_REMOTE_RE = re.compile(r"\b(work\s+from\s+home|remote|fully\s+remote)\b", re.I)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)

_WALKIN_RE = re.compile(r"\bwalk[- ]?in(?:\s+interview)?\b|\bwalkins?\b", re.I)
_WALKIN_DATE_NUMERIC_RE = re.compile(
    r"\b(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{2,4})\b"
)
_MONTHS = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t)?ember|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
_WALKIN_DATE_DAY_NAME_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:" + _MONTHS + r")\s*(?:,)?\s*(\d{2,4})\b", re.I
)
_WALKIN_DATE_MONTH_NAME_RE = re.compile(
    r"\b(?:" + _MONTHS + r")\s+(\d{1,2})(?:st|nd|rd|th)?\s*(?:,)?\s*(\d{2,4})\b", re.I
)
_WALKIN_TIME_RE = re.compile(
    r"(?:\b(?:at|from|between|time)\s*[:：]?\s*)?"
    r"(\d{1,2}(?::\d{2})?\s*[APap][Mm])\s*(?:[-–—to]\s*(\d{1,2}(?::\d{2})?\s*[APap][Mm]))?",
    re.I,
)
_VENUE_RE = re.compile(
    r"\b(?:venue|address|location)\s*[:：]\s*(.{5,220}?)"
    r"(?:\n|(?=\b(?:time|date|role|contact|phone|email|note|interview|register|timings?)\b))",
    re.I | re.S,
)
# Cities outside the user's region — a job posted there is a location mismatch.
_OUTSIDE_LOCATIONS = [
    "pune", "maharashtra", "mumbai", "delhi", "ncr", "gurugram", "gurgaon",
    "noida", "jaipur", "thane", "kolkata", "ahmedabad", "chandigarh", "bhopal",
    "indore", "lucknow", "ghaziabad", "faridabad",
]
# South-India + Tamil Nadu coverage (these count as location MATCHES even when
# not listed in PROFILE_LOCATIONS, since the daily fetch searches them).
_SOUTH_LOCATIONS = [
    "chennai", "madurai", "tamil nadu", "bengaluru", "hyderabad", "kochi",
    "coimbatore", "salem", "tiruchirappalli", "trichy", "tirunelveli", "erode",
    "vellore", "hosur", "thanjavur", "thoothukudi", "cuddalore", "dharmapuri",
    "dindigul", "krishnagiri", "kanchipuram",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _field(obj, name: str, default=None):
    """Read an attribute OR dict key from a job-like object (Job, JobCreate, dict)."""
    if obj is None:
        return default
    get = getattr(obj, "get", None)
    if callable(get):
        value = get(name, default)
        return value
    return getattr(obj, name, default)


def failed_result() -> dict:
    """Defensive fallback the caller stores when analyze_job could not run."""
    return {
        "analysis_status": "failed",
        "analyzed_at": _now_iso(),
        "llm_enriched": False,
        "reason": "analysis failed",
    }


def _match_role(title: str) -> dict:
    t = " " + " ".join((title or "").lower().split()) + " "
    for role in user_profile.roles():
        if role in t:
            return {"matched": True, "matched_role": role,
                    "reason": f"role '{role}' found in title"}
    low = (title or "").strip().lower()
    if not low:
        return {"matched": None, "matched_role": None,
                "reason": "no title to match against"}
    return {"matched": False, "matched_role": None,
            "reason": "role not in target roles"}


def _parse_experience(experience_text: str) -> tuple:
    """Return (min_years, max_years, required_text) or (None, None, '').

    Fresher/entry-level signals count as (0, 1). '3+ years' -> (3, 3)."""
    t = " " + " ".join((experience_text or "").lower().split()) + " "
    if _FRESHER_RE.search(t):
        return 0.0, 1.0, "fresher/entry-level"
    for m in _YEARS_RE.finditer(t):
        a = float(m.group(1))
        b = float(m.group(2)) if m.group(2) else a
        if a > 50 or b > 50:
            continue
        return a, b, m.group(0).strip()
    return None, None, ""


def _match_experience(experience_text: str) -> dict:
    min_years, max_years, required = _parse_experience(experience_text)
    cap = user_profile.max_experience_years()
    if min_years is None:
        return {"matched": None, "min_years": None, "max_years": None,
                "required_text": "", "reason": "experience not stated in post"}
    if min_years <= cap:
        return {"matched": True, "min_years": min_years, "max_years": max_years,
                "required_text": required,
                "reason": f"requires ~{min_years:g} yr maximum, within {cap:g}-yr profile"}
    return {"matched": False, "min_years": min_years, "max_years": max_years,
            "required_text": required,
            "reason": f"requires {min_years:g}+ yrs, over the {cap:g}-yr profile"}


def _match_skills(title: str, description: str) -> dict:
    text = " " + " ".join(f"{title or ''} {description or ''}".lower().split()) + " "
    found = [s for s in user_profile.skills() if s in text]
    if found:
        return {"matched": True, "matched_skills": found,
                "missing_skills": [], "reason": f"{len(found)} target skill(s) named"}
    # Skills are CONFIRMATORY only — they never veto an otherwise matching role.
    # A matching role with no named target skill stays "unknown", not "no match".
    return {"matched": None, "matched_skills": [], "missing_skills": [],
            "reason": "no target skill named in post"}


def _match_location(location: str, description: str) -> dict:
    loc = " " + ((location or "") or "").lower() + " "
    text = " " + " ".join(((description or "") or "").lower().split()) + " "

    # Remote/hybrid wording anywhere -> remote (a location MATCH).
    remote_hit = bool(_REMOTE_RE.search(loc) or _REMOTE_RE.search(text))
    hybrid_hit = bool(_HYBRID_RE.search(loc + text))

    # A location explicitly OUTSIDE the user's region is a mismatch.
    outside = [c for c in _OUTSIDE_LOCATIONS if c in loc]
    if outside and "remote" not in loc.split():
        return {"matched": False, "type": "on-site",
                "reason": f"location outside region ({outside[0]})"}

    south = [c for c in (_SOUTH_LOCATIONS + user_profile.locations()) if c in loc]
    if south:
        kind = "remote" if (remote_hit and not hybrid_hit) else ("hybrid" if hybrid_hit else "on-site")
        return {"matched": True, "type": kind,
                "reason": f"location matches ({south[0]}) {kind}"}

    # Remote/work-from-home wording without a city is still a location MATCH.
    if remote_hit:
        return {"matched": True, "type": "remote",
                "reason": "remote / work-from-home role"}

    if not (location or "").strip():
        return {"matched": None, "type": "unknown",
                "reason": "no location in post"}
    return {"matched": None, "type": "unknown",
            "reason": "location not clearly matched from post"}


def _match_seniority(title: str, description: str) -> dict:
    t = " " + (title or "").strip().lower() + " "
    d = " " + " ".join(((description or "") or "").lower().split()) + " "
    if _LEADERSHIP_RE.search(t):
        return {"matched": False, "level": "leadership",
                "reason": "leadership/executive title"}
    title_senior = _SENIOR_RE.search(t)
    if title_senior and not _FRESHER_RE.search(d[:200]):
        return {"matched": False, "level": "senior",
                "reason": f"senior title ('{title_senior.group(0)}')"}
    if _FRESHER_RE.search(t + " " + d[:200]):
        return {"matched": True, "level": "entry",
                "reason": "entry/fresher-signal in post"}
    if _SENIOR_RE.search(d[:300]):
        return {"matched": False, "level": "senior",
                "reason": "senior responsibility described"}
    if title_senior:
        return {"matched": True, "level": "mid",
                "reason": "senior word but fresher-friendly post"}
    if not (title or "").strip():
        return {"matched": None, "level": "unknown",
                "reason": "no title"}
    return {"matched": True, "level": "mid",
            "reason": "no seniority signal in title"}


def _normalize_numeric_date(pieces) -> str:
    a, b, c = pieces
    d1, d2, year = int(a), int(b), int(c)
    # Indian posting style is dd-mm-yyyy; prefer that whenever unambiguous.
    # 10-25-2026 (mm-dd) -> d2>12 -> day=d2, month=d1. Everything else dd-mm.
    if d2 > 12:
        day, month = d2, d1
    else:
        day, month = d1, d2
    y = year if year >= 100 else (2000 + year)
    if month < 1 or month > 12 or day < 1 or day > 31:
        return f"{d1}-{d2}-{c}"
    return f"{y:04d}-{month:02d}-{day:02d}"


def _walkin_date_hint(t: str) -> str:
    """Best-effort date extraction for a walk-in post. Prefers a date that
    follows 'date/on/interview' wording; falls back to the first numeric date."""
    found = list(_WALKIN_DATE_NUMERIC_RE.finditer(t))
    for m in found:
        before = t[max(0, m.start() - 40): m.start()].lower()
        if re.search(r"\b(date|on|interview|walk[- ]?in)\b", before):
            return _normalize_numeric_date(m.groups())
    for m in found:
        return _normalize_numeric_date(m.groups())
    for re_ in (_WALKIN_DATE_DAY_NAME_RE, _WALKIN_DATE_MONTH_NAME_RE):
        m = re_.search(t)
        if m:
            return m.group(0).strip()
    return None


def _extract_walkin(text: str) -> dict:
    t = " ".join((text or "").split())
    if not _WALKIN_RE.search(t):
        return {"is_walk_in": False, "date": None, "time": None, "venue": None}
    time_parts = None
    m = _WALKIN_TIME_RE.search(t)
    if m:
        time_parts = m.group(0).strip()
    venue = None
    m = _VENUE_RE.search(t)
    if m:
        venue = " ".join(m.group(1).split()).strip()[:220] or None
    return {"is_walk_in": True, "date": _walkin_date_hint(t),
            "time": time_parts, "venue": venue}


def _overall(role, experience, seniority, location, skills) -> dict:
    """Compose the single overall_match verdict from the four AUTHORITATIVE
    sections (role, experience, seniority, location). skills is confirmatory
    only and never vetoes — a matching role with unlisted skills stays a match.

    Rule:
      - any decisive False among the four  -> overall False
      - role True + experience True + location True (+ seniority not False)
        -> overall True
      - role True but experience/location undecided (no decisive False)
        -> overall None (needs review)
      - nothing decided -> overall None/low"""
    for name, section in (("role", role), ("experience", experience),
                          ("seniority", seniority), ("location", location)):
        if section.get("matched") is False:
            reason = section.get("reason") or name
            return {"is_match": False, "confidence": "high",
                    "summary": f"No match: {name} ({reason})"}
    decided = sum(
        1 for section in (role, experience, seniority, location)
        if section.get("matched") is True
    )
    if role.get("matched") is True and experience.get("matched") is True \
            and location.get("matched") is True and decided >= 3:
        return {"is_match": True, "confidence": "high",
                "summary": f"{decided} of 4 key signals match the profile."}
    if role.get("matched") is True and decided >= 2:
        return {"is_match": None, "confidence": "medium",
                "summary": "Core signals match but some details unknown (manual review)."}
    if not (role.get("matched") is True or experience.get("matched") is True):
        return {"is_match": None, "confidence": "low",
                "summary": "Not enough information to judge the match."}
    return {"is_match": None, "confidence": "medium",
            "summary": "Signal partly unclear (manual review)."}


def analyze_job(job) -> dict:
    """Analyze a job-like object (JobCreate / Job / dict) and return the stored
    schema dict. NEVER raises — hard failures degrade to failed_result()."""
    logger.debug("JOB_ANALYSIS_STARTED job=%s", getattr(job, "id", None))
    try:
        title = _field(job, "title") or ""
        location = _field(job, "location") or ""
        description = _field(job, "description") or ""
        experience_text = _field(job, "experience") or ""

        role = _match_role(title)
        experience = _match_experience(f"{experience_text} {description}")
        skills = _match_skills(title, description)
        location_res = _match_location(location, description)
        seniority = _match_seniority(title, description)
        walk_in = _extract_walkin(f"{title} {description}")

        result = {
            "analysis_status": "ok",
            "analyzed_at": _now_iso(),
            "llm_enriched": False,
            "role": role,
            "experience": experience,
            "skills": skills,
            "location": location_res,
            "seniority": seniority,
            "walk_in": walk_in,
        }

        # --- Deterministic trial summary from whatever the core decided ----
        tentative = _overall(role, experience, seniority, location_res, skills)
        result["overall_match"] = tentative

        # --- Optional LLM enrichment for fields the core left unknown ---------
        use_llm = Config.JOB_ANALYSIS_USE_LLM != "false"
        if use_llm and Config.GROQ_API_KEY.strip():
            try:
                from services import llm as _llm

                llm_out = _llm.analyze_job(f"{title}\n{location}\n{experience_text}\n{description}")
                result = _merge_llm(result, llm_out)
            except Exception as e:  # never let the LLM sink the analysis
                logger.debug("JOB_ANALYSIS llm enrichment skipped: %s", e)
        logger.debug("JOB_ANALYSIS_COMPLETED job=%s", getattr(job, "id", None))
        return result
    except Exception:
        logger.exception("JOB_ANALYSIS_FAILED")
        return failed_result()


def _merge_llm(result: dict, llm_out: dict) -> dict:
    """Fill ONLY core-unknown fields from validated LLM output; re-checks the
    overall verdict when an undecided section became decidable."""
    if not isinstance(llm_out, dict) or not llm_out:
        return result
    has_signal = (
        llm_out.get("role_matched") is not None
        or llm_out.get("experience_min_years") is not None
        or bool(llm_out.get("skills_matched"))
        or llm_out.get("location_matched") is not None
        or llm_out.get("seniority_level") in ("entry", "mid", "senior", "leadership")
        or llm_out.get("walk_in", {}).get("is_walk_in")
        or bool(llm_out.get("summary"))
    )
    if not has_signal:
        return result
    changed = False
    role = result["role"]
    if role.get("matched") is None and llm_out.get("role_matched") is not None:
        role["matched"] = bool(llm_out["role_matched"])
        role["matched_role"] = llm_out.get("matched_role") or role.get("matched_role")
        role["reason"] = "llm: " + (role["reason"] or "no title signal").replace("no title", "unclear")
        changed = True

    exp = result["experience"]
    if exp.get("min_years") is None and llm_out.get("experience_min_years") is not None:
        mmin = llm_out["experience_min_years"]
        mmax = llm_out.get("experience_max_years") or mmin
        exp["min_years"], exp["max_years"] = mmin, mmax
        exp["matched"] = mmin <= user_profile.max_experience_years()
        exp["reason"] = f"llm: requires ~{mmin:g} yrs"
        changed = True

    skills = result["skills"]
    if skills.get("matched") is None and llm_out.get("skills_matched"):
        matched_skills = [s for s in llm_out["skills_matched"] if s]
        if matched_skills:
            skills["matched"] = True
            skills["matched_skills"] = matched_skills[:10]
            skills["reason"] = "llm: skills named in post"
            changed = True

    loc = result["location"]
    if loc.get("matched") is None and llm_out.get("location_matched") is not None:
        loc["matched"] = bool(llm_out["location_matched"])
        loc["type"] = (llm_out.get("location_type") or loc["type"])
        if loc["type"] not in ("remote", "hybrid", "on-site", "unknown"):
            loc["type"] = "unknown"
        loc["reason"] = "llm: " + (loc["reason"] or "location unclear")
        changed = True

    sen = result["seniority"]
    if sen.get("level") == "unknown" and llm_out.get("seniority_level") in (
            "entry", "mid", "senior", "leadership"):
        sen["level"] = llm_out["seniority_level"]
        sen["matched"] = llm_out["seniority_level"] in ("entry", "mid")
        sen["reason"] = "llm: " + (sen["reason"] or "seniority unclear")
        changed = True

    wk = result["walk_in"]
    if not wk.get("is_walk_in") and llm_out.get("walk_in", {}).get("is_walk_in"):
        llm_wk = llm_out["walk_in"]
        wk["is_walk_in"] = True
        wk["date"] = wk.get("date") or llm_wk.get("date") or None
        wk["time"] = wk.get("time") or llm_wk.get("time") or None
        wk["venue"] = wk.get("venue") or llm_wk.get("venue") or None
        changed = True

    if changed:
        result["overall_match"] = _overall(
            result["role"], result["experience"], result["seniority"],
            result["location"], result["skills"],
        )
        result["llm_enriched"] = True
        if not result["overall_match"].get("summary") and llm_out.get("summary"):
            result["overall_match"]["summary"] = llm_out["summary"][:400]
    return result