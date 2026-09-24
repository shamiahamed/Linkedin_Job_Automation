"""Job Intelligence Agent — evaluates each ingested job against the existing
deterministic Job Analysis (Phase 2) and the centralized user profile.

Pure enrichment: it NEVER emails, notifies, auto-applies, changes status, dedupes,
deletes, or bypasses any existing gate. It only produces a `job_intelligence` JSON
document stored on the Job row (see models.Job.job_intelligence).

Authoritative vs narrative split:
  - The match booleans (role/experience/location/seniority/skill), the walk-in
    flag, the matched_roles/matched_skills lists, and the match_score are all
    computed HERE from the deterministic `job_analysis` fields. The LLM can never
    flip one of them — a deterministic `experience_match=false` stays false even
    if the post otherwise looks attractive.
  - The LLM (services.llm.intelligence_note) may only enrich the NARRATIVE:
    missing_skills suggestions, a one-line summary, extra concerns. Its output is
    type-validated and bounded before merging.

Match score (deterministic, documented weights — total 100):
  role_match        30   (must match a target role)
  experience_match  25   (requirement within the profile's max years)
  location_match    20   (in-region / remote)
  skill_match       15   (target skills named; confirmatory, never vetoes)
  seniority_match   10   (entry/mid vs senior/leadership)
  Value per signal: matched=full weight, unknown=none=half weight (int floor),
  not-matched=0. So all-matched = 100, all-unknown = 47.

match_status (deterministic authority first):
  - any of role/experience/location/seniority is a decisive False -> "not_matched"
  - any of those four is unknown (None)                        -> "needs_review"
  - otherwise score >= 70 -> "matched"; >= 45 -> "needs_review"; else "not_matched"

evaluate_job() NEVER raises: on missing/invalid deterministic analysis or any
unexpected error it returns a safe `{"intelligence_status": "unavailable", ...}`
so the ingest pipeline always survives. Logs never include job descriptions or
secrets.
"""
import logging
from datetime import datetime, timezone

from config import Config
from services import user_profile

logger = logging.getLogger("uvicorn.error")

# (signal, weight) — documented scoring ruler (see module docstring).
_WEIGHTS = {
    "role_match": 30,
    "experience_match": 25,
    "location_match": 20,
    "skill_match": 15,
    "seniority_match": 10,
}
_MATCHED_BAND = 70
_REVIEW_BAND = 45
_AUTHORITATIVE = ("role_match", "experience_match", "location_match", "seniority_match")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tri(value):
    """Keep bool|None as-is (never coerce 0/1 into bools by accident)."""
    if value is True or value is False:
        return value
    return None


def _unavailable(reason: str = "") -> dict:
    return {
        "intelligence_status": "unavailable",
        "evaluated_at": _now_iso(),
        "llm_enriched": False,
        "match_status": "unavailable",
        "reason": reason,
    }


def evaluate_job(job_text: str, job_analysis: dict, user_profile_data: dict = None) -> dict:
    """Evaluate a job described by `job_text` against the deterministic
    `job_analysis` (Phase 2 schema) and the applicant profile.

    Returns the stored JSON schema dict. NEVER raises — hard failures degrade to
    the safe "unavailable" fallback so job ingestion always survives.
    """
    logger.debug("JOB_INTELLIGENCE_STARTED")
    try:
        analysis = _safe_analysis(job_analysis)
        if analysis is None:
            # No deterministic signals to evaluate against — refuse to invent any.
            return _unavailable("no deterministic job analysis to evaluate against")
        profile = user_profile_data if isinstance(user_profile_data, dict) else user_profile.snapshot()
        result = _deterministic(analysis)

        use_llm = Config.JOB_INTELLIGENCE_USE_LLM != "false"
        if use_llm and (Config.GROQ_API_KEY or "").strip():
            try:
                from services import llm as _llm

                note = _llm.intelligence_note(job_text or "", result, profile)
                result = _merge_note(result, note)
            except Exception as e:  # never let the LLM sink the evaluation
                logger.debug("JOB_INTELLIGENCE llm skip: %s", e)
        logger.debug("JOB_INTELLIGENCE_COMPLETED status=%s",
                     result.get("match_status"))
        return result
    except Exception:
        logger.exception("JOB_INTELLIGENCE_FAILED")
        return _unavailable("evaluation failed")


def _safe_analysis(job_analysis) -> dict:
    """A usable deterministic analysis dict, or None. Only well-shaped Phase 2
    output counts — anything else means we have no authority to decide."""
    if not isinstance(job_analysis, dict) or job_analysis.get("analysis_status") != "ok":
        return None
    for section in ("role", "experience", "skills", "location", "seniority", "walk_in"):
        if not isinstance(job_analysis.get(section), dict):
            return None
    return job_analysis


def _section(analysis: dict, name: str) -> dict:
    return analysis.get(name) or {}


def _deterministic(analysis: dict) -> dict:
    role = _section(analysis, "role")
    experience = _section(analysis, "experience")
    skills = _section(analysis, "skills")
    location = _section(analysis, "location")
    seniority = _section(analysis, "seniority")
    walk_in = _section(analysis, "walk_in")

    signals = {
        "role_match": _tri(role.get("matched")),
        "experience_match": _tri(experience.get("matched")),
        "location_match": _tri(location.get("matched")),
        "seniority_match": _tri(seniority.get("matched")),
        "skill_match": _tri(skills.get("matched")),
    }

    score = 0
    for key, weight in _WEIGHTS.items():
        value = signals[key]
        if value is True:
            score += weight
        elif value is None:
            score += weight // 2  # unknown = credit half, int floor

    # Deterministic authority: any decisive False among the authoritative
    # signals is an immediate "not_matched"; an unknown one needs review.
    status = "matched"
    for key in _AUTHORITATIVE:
        if signals[key] is False:
            status = "not_matched"
            break
        if signals[key] is None:
            status = "needs_review"
    if status == "matched":
        status = ("matched" if score >= _MATCHED_BAND
                  else "needs_review" if score >= _REVIEW_BAND else "not_matched")

    matched_roles = [role.get("matched_role")] if signals["role_match"] is True and role.get("matched_role") else []
    matched_skills = [str(s).strip() for s in (skills.get("matched_skills") or []) if str(s).strip()][:15]
    missing_skills = [str(s).strip() for s in (skills.get("missing_skills") or []) if str(s).strip()][:15]

    reasons = []
    concerns = []
    if signals["role_match"] is True and matched_roles:
        reasons.append(f"Role matches a target role ({matched_roles[0]})")
    elif signals["role_match"] is False:
        concerns.append("Role is not in the target roles")
    if signals["experience_match"] is True:
        reasons.append("Experience requirement is within the user's range")
    elif signals["experience_match"] is False:
        concerns.append("Requires more experience than the user's profile allows")
    if signals["location_match"] is True:
        loc_type = location.get("type")
        base = "Location matches the user's region"
        reasons.append(f"{base} ({loc_type})" if loc_type and loc_type != "unknown" else base)
    elif signals["location_match"] is False:
        concerns.append("Location is outside the user's region")
    if signals["seniority_match"] is True:
        reasons.append("Seniority level fits the profile")
    elif signals["seniority_match"] is False:
        concerns.append("Senior/leadership level — outside the user's target")
    if signals["skill_match"] is True and matched_skills:
        reasons.append(f"{len(matched_skills)} preferred skill(s) named in the post")
    if walk_in.get("is_walk_in"):
        reasons.append("Walk-in interview (check date/time/venue)")

    return {
        "intelligence_status": "ok",
        "evaluated_at": _now_iso(),
        "llm_enriched": False,
        "match_status": status,
        "match_score": int(score),
        "role_match": signals["role_match"],
        "skill_match": signals["skill_match"],
        "experience_match": signals["experience_match"],
        "location_match": signals["location_match"],
        "seniority_match": signals["seniority_match"],
        "walk_in": bool(walk_in.get("is_walk_in")),
        "matched_roles": matched_roles,
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "reasons": reasons,
        "concerns": concerns,
        "summary": "",
    }


def _merge_note(result: dict, note: dict) -> dict:
    """Merge the (validated, bounded) LLM narrative. NEVER touches the booleans,
    the score, the status, or the deterministic skill lists — narrative only."""
    if not isinstance(note, dict) or not note:
        return result
    changed = False

    llm_missing = [str(s).strip() for s in (note.get("missing_skills") or []) if str(s).strip()]
    if llm_missing:
        seen = list(result["missing_skills"])
        for s in llm_missing:
            low = s.lower()
            if low not in [x.lower() for x in seen]:
                seen.append(s)
                if len(seen) >= 15:
                    break
        if seen != result["missing_skills"]:
            result["missing_skills"] = seen
            changed = True

    note_summary = str(note.get("summary") or "").strip()[:400]
    if note_summary:
        result["summary"] = note_summary
        changed = True

    extra = [str(s).strip() for s in (note.get("additional_concerns") or []) if str(s).strip()]
    for s in extra:
        if s and s not in result["concerns"] and len(result["concerns"]) < 6:
            result["concerns"].append(s)
            changed = True

    if changed:
        result["llm_enriched"] = True
    return result