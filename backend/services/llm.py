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
import requests
from config import Config

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_model = Config.GROQ_MODEL
_draft_cache = {}


def _enabled() -> bool:
    return bool((Config.GROQ_API_KEY or "").strip() and _model)


def _chat(messages, json_mode=False, max_tokens=2048):
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
    resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=40)
    resp.raise_for_status()
    data = resp.json()
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    return (msg.get("content") or "").strip()


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
    """Return a role-specific, personalized cover paragraph, or None on any failure.
    Experience framing adapts to the role (fresher role -> fresher, 1-year role ->
    1 year); additional_message (free-form user guidance) steers the subject/body."""
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
        f"\nThe applicant wants this email to highlight the following (follow it closely): {guidance}\n"
        if guidance else ""
    )
    prompt = (
        f"Write ONE personalized, professional cover-letter paragraph (100–150 words) for "
        f"a job application to the role '{title}' at '{company}'. The role's stated requirement "
        f"is: \"{role_req}\". Present the applicant as {level} — match the experience framing to "
        "the role exactly (entry-level role -> fresher tone; ~1-year role -> one year of "
        "hands-on experience). Never claim more experience than one year. The applicant is "
        "Shamim Ahamed J, with FOCAL points: Python/FastAPI/Django backends, data analytics "
        "(Power BI, SQL), manual + automated QA (Selenium, pytest), and IT network support. "
        "Match the paragraph to the role and the company, mention an appropriate relevant "
        f"skill, keep it humble and specific.{guidance_part} No greetings or closing, no "
        "'I am writing to ' — just the persuasive body paragraph. Return ONLY the plain text."
    )
    try:
        text = _chat([{"role": "user", "content": prompt}], max_tokens=1600)
        text = (text or "").strip()
        text = re.sub(r"^\"|\"$", "", text)
        _draft_cache[key] = text or None
        return _draft_cache[key]
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
        _subject_cache[key] = text or None
        return _subject_cache[key]
    except Exception:
        return None