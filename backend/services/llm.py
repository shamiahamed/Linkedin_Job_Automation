"""
Optional Groq LLM enrichment layer.

Turns on only when Config.GROQ_API_KEY is set (see backend/.env). Every call is
defensively wrapped: any failure returns None/{}, so the app keeps working with
the built-in regex + OCR heuristics when Groq is unreachable or rate-limited.

Two jobs:
  1. extract_job(text)  -> structured fields for weak/image-captured posts
  2. draft_email(job)    -> role-specific cover paragraph for application emails
"""
import json
import re
import time
import requests
from config import Config

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_model = Config.GROQ_MODEL
_draft_cache = {}


def _enabled() -> bool:
    return bool((Config.GROQ_API_KEY or "").strip() and _model)


def _chat(messages, json_mode=False, max_tokens=2048, attempts=3, timeout=90):
    if not _enabled():
        return None
    payload = {
        "model": _model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {Config.GROQ_API_KEY.strip()}",
        "Content-Type": "application/json",
    }
    last_err = None
    for i in range(attempts):
        try:
            resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            content = (msg.get("content") or "").strip()
            if content:
                return content
            # Groq sometimes returns empty content for short-output prompts;
            # retry a couple of times before giving up.
            last_err = RuntimeError("empty content")
        except requests.exceptions.HTTPError as e:
            last_err = e
            if getattr(e.response, "status_code", None) == 429:
                time.sleep(5)
                continue
            raise
        except Exception as e:
            last_err = e
        time.sleep(2)
    return None


def _parse_json(raw):
    if not raw:
        return {}
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            return json.loads(m.group(0)) if m else {}
        except Exception:
            return {}


def _emails_from(obj, raw):
    out = []
    if isinstance(obj, list):
        for v in obj:
            s = str(v or "").strip()
            if s and "@" in s:
                out.append(s)
    elif isinstance(obj, str):
        em = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", obj)
        out.extend(em)
    if not out:
        out = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", raw)
    seen, res = set(), []
    for e in out:
        e = e.strip().lower()
        if e and e not in seen:
            seen.add(e)
            res.append(e)
    return res


def extract_job(text) -> dict:
    """Ask Groq to turn a messy/OCR'd job post into clean structured fields."""
    if not _enabled() or not (text or "").strip():
        return {}
    prompt = (
        "You extract hiring details from a raw LinkedIn job post (may contain OCR text). "
        "Return ONLY a JSON object with these keys:\n"
        '{"title": string, "company": string, "location": string, "experience_range": string, '
        '"salary": string, "emails": [string], "phones": [string], "apply_link": string}\n'
        "Rules: title = the single job role (e.g. 'QA Automation Intern'), drop emoji/decorations. "
        "company = the real hiring company name or empty. emails/phones = only explicit contacts, "
        "exclude generic @example.com. apply_link = the first real https URL that looks like a "
        "careers/apply page or lnkd.in link, else empty. Use '' or [] when unknown.\n\nPOST:\n"
        + (text or "")[:6000]
    )
    try:
        raw = _chat(
            [{"role": "user", "content": prompt}],
            json_mode=True,
            max_tokens=1600,
        )
        obj = _parse_json(raw)
        if not obj:
            return {}
        return {
            "title": str(obj.get("title") or "").strip(),
            "company": str(obj.get("company") or "").strip(),
            "location": str(obj.get("location") or "").strip(),
            "experience": str(obj.get("experience_range") or "").strip(),
            "salary": str(obj.get("salary") or "").strip(),
            "emails": _emails_from(obj.get("emails"), text)[:3],
            "phones": _phones(obj, text)[:3],
            "apply_link": str(obj.get("apply_link") or "").strip(),
        }
    except Exception:
        return {}


def _phones(obj, raw):
    out = []
    phon = obj.get("phones")
    if isinstance(phon, list):
        out = [str(p).strip() for p in phon if str(p).strip()]
    elif isinstance(phon, str) and phon.strip():
        out = [phon.strip()]
    if not out:
        p = re.search(r"(?:\+?\d[\d\s-]{8,}\d)", raw)
        if p:
            out = [p.group(0).strip()]
    return out


_FRESHER_RE = re.compile(
    r"\b(freshers?|entry[- ]?level|0 experience|no experience|recent graduates?|"
    r"graduate trainee|passouts?|0[-–]?1\s*years?)\b", re.I
)


def role_level(job) -> str:
    """Frame the applicant's experience to MATCH the role, not a fixed constant:
    - entry-level/fresher roles -> present as a fresher
    - 0-1 / 1-year roles        -> present as 1 year of hands-on experience
    - anything asking for more  -> honest ~1 year hands-on experience
    """
    base = " ".join([job.experience or "", (job.description or "")[:400]]).lower()
    if _FRESHER_RE.search(base):
        return "a fresher (entry-level) who recently completed hands-on projects and internships"
    return f"{Config.YOUR_EXPERIENCE_YEARS or '1'} year of hands-on professional experience"


def draft_email(job, additional_message: str = "") -> str:
    """Return a COMPLETE application email body (plain text, greeting -> closing),
    or None on any failure. Experience framing adapts to the role (fresher role ->
    fresher, ~1-year role -> 1 year). additional_message is a direct user
    instruction that can ADD or REMOVE specific sections (e.g. 'remove GitHub')."""
    if not _enabled() or not job:
        return None
    key = f"{job.title or ''}|{job.company or ''}|{(additional_message or '').strip()}"
    if key in _draft_cache:
        return _draft_cache[key]
    title = (job.title or "").strip() or "this position"
    company = (job.company or "").strip() or "your company"
    level = role_level(job)
    role_req = " ".join([(job.experience or "").strip(), (job.description or "")[:350]]).strip()[:500]
    guidance = (additional_message or "").strip()
    guidance_part = (
        f"The applicant's note below is a DIRECT user instruction — follow it exactly; "
        f"it may ask you to add or remove specific sections (e.g. remove GitHub):\n"
        f"\"{guidance}\"\n\n"
        if guidance else ""
    )
    prompt = (
        f"Write the COMPLETE body of a professional job-application email (plain text) "
        f"for the role '{title}' at '{company}'. The role's stated requirement is: "
        f"\"{role_req}\".\n"
        f"{guidance_part}"
        f"RULES:\n"
        f"- First line MUST be: Dear HR,\n"
        f"- Then 2–3 short paragraphs, each separated by a blank line.\n"
        f"- Present the applicant as {level}; never claim more than one year of experience.\n"
        f"- Applicant: Shamim Ahamed J. Mention skills ONLY from the applicant's background "
        f"that are DIRECTLY asked for in this role's title or requirements. Pick AT MOST "
        f"ONE or TWO skill areas, and ONLY if actually related to THIS role. NEVER list "
        f"multiple background areas together. If the role does not match any of 'backend "
        f"(Python/FastAPI/Django, PostgreSQL, Docker)', 'data analytics (Power BI, SQL)', "
        f"'QA/testing (Selenium, pytest)', or 'IT network/support', write a short simple "
        f"paragraph about being detail-oriented, willing to learn, and ready to contribute "
        f"— do not invent the applicant's skills.\n"
        f"- For a customer-support/support role, write ONLY about communication, diagnosing "
        f"and resolving issues, and helping users; keep any technical mention brief or omit it.\n"
        f"- Keep the language SIMPLE and directly about '{title}' — no generic filler.\n"
        f"- By DEFAULT do not add any GitHub/project links. The ONLY exception: if the "
        f"applicant's note explicitly asks to include GitHub/projects, then include them.\n"
        f"- Do NOT include availability phrases like 'available at your convenience', "
        f"or filler like 'I hope this email finds you well' or 'I came across this opportunity'.\n"
        f"- End with the sentence: Thank you for considering my application.\n"
        f"- Do NOT include a sign-off or signature (the sender's name/phone/email are added "
        f"by the system afterwards).\n"
        f"Return ONLY the email body plain text."
    )
    try:
        text = _chat([{"role": "user", "content": prompt}], max_tokens=700)
        text = (text or "").strip()
        text = re.sub(r"^\"|\"$", "", text)
        # Only cache successful generations; a transient Groq failure (None)
        # must NOT poison this job+guidance combo for the rest of the process.
        if text:
            _draft_cache[key] = text
            return text
        return None
    except Exception:
        return None


_subject_cache = {}

def draft_subject(job, additional_message: str = "") -> str:
    """Return a short, role-matched application email subject line, or None on failure."""
    if not _enabled() or not job:
        return None
    key = f"{job.title or ''}|{job.company or ''}|{(additional_message or '').strip()}"
    if key in _subject_cache:
        return _subject_cache[key]
    title = (job.title or "").strip() or "this position"
    company = (job.company or "").strip() or "your company"
    level = role_level(job)
    guidance = (additional_message or "").strip()
    guidance_part = (
        f"\nThe applicant's note to reflect: {guidance}" if guidance else ""
    )
    prompt = (
        f"Write ONE short, professional email subject line (max 12 words, no quotes) for a "
        f"job application to the role '{title}' at '{company}' from Shamim Ahamed J, presented "
        f"as {level}. Match the tone to the role; never claim more than one year of "
        f"experience.{guidance_part} "
        "Return ONLY the plain subject text."
    )
    try:
        text = _chat([{"role": "user", "content": prompt}], max_tokens=256)
        text = (text or "").strip().strip('"')
        if text:
            _subject_cache[key] = text
            return text
        return None
    except Exception:
        return None


_followup_cache = {}


def draft_followup(job, applied_date: str = "", extra: str = "") -> str:
    """Return a short, polite follow-up email body (plain text) for a job the user
    already applied to, or None on failure. extra is a direct instruction (e.g.
    'mention you are still interested')."""
    if not _enabled() or not job:
        return None
    key = f"{job.title or ''}|{job.company or ''}|{applied_date}|{extra}"
    if key in _followup_cache:
        return _followup_cache[key]
    title = (job.title or "").strip() or "the position"
    company = (job.company or "").strip() or "your company"
    applied_line = f"I submitted my application on {applied_date}." if applied_date else "I recently submitted my application."
    extra_line = f"\nThe applicant's instruction: {extra}" if extra else ""
    prompt = (
        f"Write a short, polite job-application follow-up email body (plain text, "
        f"3-4 sentences) for the role '{title}' at '{company}'. {applied_line} Ask "
        f"for a brief update on the status, keep the tone professional and not pushy, "
        f"and do not repeat the whole cover letter.{extra_line}\n"
        f"RULES:\n"
        f"- First line MUST be: Dear HR,\n"
        f"- End with the sentence: Thank you for your time.\n"
        f"- Do NOT include a sign-off or signature (added by the system).\n"
        f"- Do NOT use filler like 'I hope this email finds you well'.\n"
        f"Return ONLY the email body plain text."
    )
    try:
        text = _chat([{"role": "user", "content": prompt}], max_tokens=400)
        text = (text or "").strip()
        text = re.sub(r"^\"|\"$", "", text)
        if text:
            _followup_cache[key] = text
            return text
        return None
    except Exception:
        return None


def _as_float(value, lo=0.0, hi=50.0):
    """Coerce an LLM-provided number to a sane float, or None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def _as_bool(value):
    """True/False from an LLM-provided boolean OR text 'yes'/'no'/'true'/'false'."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("yes", "true", "1"):
            return True
        if v in ("no", "false", "0"):
            return False
    return None


def analyze_job(text) -> dict:
    """Optional Groq enrichment for the Job Analysis Agent.

    Returns the LLM's independent view of the post as a sparse dict (or {} on
    any failure / when disabled). The Job Analyzer's deterministic core decides
    what is authoritative; it VALIDATES this output and only uses values for
    fields the core itself left undecided (None). The LLM therefore never
    overrides a reasoned 'match/no-match' — it only fills gaps.

    Defensive wrapper for the whole call (mirrors extract_job), so a Groq
    timeout/rate-limit/JSON error degrades to {} without raising.
    """
    if not _enabled() or not (text or "").strip():
        return {}
    prompt = (
        "You are a job-analysis assistant. Given a job posting, answer ONLY from "
        "the text. Return ONLY a JSON object with EXACTLY these keys:\n"
        '{'
        '"role_matched": boolean-or-null, '
        '"matched_role": string, '
        '"experience_min_years": number-or-null, '
        '"experience_max_years": number-or-null, '
        '"skills_matched": [strings], '
        '"skills_missing": [strings], '
        '"location_matched": boolean-or-null, '
        '"location_type": "remote" | "hybrid" | "onsite" | "unknown", '
        '"seniority_level": "entry" | "mid" | "senior" | "leadership" | "unknown", '
        '"walk_in": { "is_walk_in": boolean, "date": string-or-null, '
        '"time": string-or-null, "venue": string-or-null }, '
        '"summary": string'
        '}\n'
        "Rules: role_matched = whether the ROLE fits a junior software/data/QA "
        "career (python/backend/react/full-stack/QA/data-analyst); "
        "experience_min/max_years = the years REQUIRED, null when not stated; "
        "skills_matched = only skills explicitly named in the text; "
        "location_matched = whether the work location is Chennai/Tamil Nadu/"
        "Madurai or Remote (null when unclear); walk_in only when the post is a "
        "walk-in interview — extract exact date/time/venue or null. summary = "
        "one short sentence on overall fit. Use null / [] when unknown.\n\nPOST:\n"
        + (text or "")[:6000]
    )
    try:
        raw = _chat(
            [{"role": "user", "content": prompt}],
            json_mode=True,
            max_tokens=1600,
            timeout=45,
        )
        obj = _parse_json(raw)
        if not obj:
            return {}
        walk_in = obj.get("walk_in")
        if not isinstance(walk_in, dict):
            walk_in = {}
        return {
            "role_matched": _as_bool(obj.get("role_matched")),
            "matched_role": str(obj.get("matched_role") or "").strip()[:120],
            "experience_min_years": _as_float(obj.get("experience_min_years")),
            "experience_max_years": _as_float(obj.get("experience_max_years")),
            "skills_matched": [str(s).strip()[:60] for s in (obj.get("skills_matched") or []) if str(s).strip()][:15],
            "skills_missing": [str(s).strip()[:60] for s in (obj.get("skills_missing") or []) if str(s).strip()][:15],
            "location_matched": _as_bool(obj.get("location_matched")),
            "location_type": str(obj.get("location_type") or "unknown").strip().lower()[:16],
            "seniority_level": str(obj.get("seniority_level") or "unknown").strip().lower()[:16],
            "walk_in": {
                "is_walk_in": bool(_as_bool(walk_in.get("is_walk_in"))),
                "date": str(walk_in.get("date") or "").strip()[:60],
                "time": str(walk_in.get("time") or "").strip()[:60],
                "venue": str(walk_in.get("venue") or "").strip()[:240],
            },
            "summary": str(obj.get("summary") or "").strip()[:400],
        }
    except Exception:
        return {}


def _list_field(obj: dict, key: str, cap: int = 8, max_len: int = 80):
    """Normalise an LLM list field: handles list, comma-string, or None, and
    bounds the length so the stored result stays small and stable."""
    raw = obj.get(key)
    if isinstance(raw, str):
        items = [part.strip() for part in re.split(r"[,;]", raw) if part.strip()]
    elif isinstance(raw, (list, tuple)):
        items = [str(s).strip() for s in raw if str(s).strip()]
    else:
        items = []
    return [s[:max_len] for s in items][:cap]


def intelligence_note(job_text: str, deterministic: dict, profile: dict = None) -> dict:
    """Optional Groq narrative enrichment for the Job Intelligence Agent.

    The LLM may ONLY explain/annotate — it is given the deterministic verdict and
    asks to return missing skills, a one-line summary, and extra concerns. The job
    text and the deterministic bools are inputs; the agent never lets this output
    flip a boolean or invent the score. Returns {} on any failure / when disabled,
    or when the payload is empty, so the intelligence agent degrades to
    deterministic-only without raising.

    No secrets or full descriptions are logged anywhere in this path.
    """
    if not _enabled() or not (job_text or "").strip():
        return {}
    profile_lines = ""
    if isinstance(profile, dict):
        profile_lines = (
            "\nCandidate profile: roles=%s skills=%s locations=%s max_exp_years=%s"
            % (
                ",".join(profile.get("roles") or []),
                ",".join(profile.get("skills") or []),
                ",".join(profile.get("locations") or []),
                profile.get("max_experience_years"),
            )
        )
    prompt = (
        "You annotate a job posting for the candidate's own review. Return ONLY a "
        "JSON object with EXACTLY these keys:\n"
        '{"missing_skills": [string], "summary": string, "additional_concerns": [string]}\n'
        "Rules: missing_skills = up to 8 skills the POST asks for that the candidate "
        "profile does NOT list (the candidate's skills are given below) — do not "
        "list skills the candidate already has. summary = ONE short sentence on why "
        "this job is or is not a good fit, based ONLY on the facts given. "
        "additional_concerns = up to 5 short, factual concerns (e.g. an out-of-region "
        "city, seniority, or a must-have skill gap).\n"
        "Deterministic verdict ALREADY DECIDED (never contradict it):\n"
        + _llm_facts(deterministic)
        + profile_lines
        + "\n\nPOST:\n"
        + (job_text or "")[:6000]
    )
    try:
        timeout = int(getattr(Config, "JOB_INTELLIGENCE_TIMEOUT", "45") or "45")
        timeout = min(120, max(5, timeout))
        raw = _chat(
            [{"role": "user", "content": prompt}],
            json_mode=True,
            max_tokens=700,
            timeout=timeout,
        )
        obj = _parse_json(raw)
        if not obj:
            return {}
        return {
            "missing_skills": _list_field(obj, "missing_skills", cap=8, max_len=80),
            "summary": str(obj.get("summary") or "").strip()[:400],
            "additional_concerns": _list_field(obj, "additional_concerns", cap=5, max_len=160),
        }
    except Exception:
        return {}


def _llm_facts(deterministic: dict) -> str:
    """Compact, non-sensitive summary of the deterministic verdict for the LLM."""
    keys = ("role_match", "experience_match", "location_match",
            "seniority_match", "skill_match", "walk_in")
    parts = []
    for k in keys:
        v = deterministic.get(k)
        if v is None:
            continue
        parts.append(f"{k}={v}")
    matched_roles = ",".join(deterministic.get("matched_roles") or [])
    matched_skills = ",".join(deterministic.get("matched_skills") or [])
    missing_skills = ",".join(deterministic.get("missing_skills") or [])
    if matched_roles:
        parts.append(f"matched_roles={matched_roles}")
    if matched_skills:
        parts.append(f"matched_skills={matched_skills}")
    if missing_skills:
        parts.append(f"missing_skills={missing_skills}")
    return "; ".join(parts) if parts else "no deterministic signals"