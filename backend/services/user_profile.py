"""Central applicant profile for the Job Analysis Agent.

Every matcher (deterministic core + LLM enrichment) reads the profile from here
instead of hard-coding role/skill/location lists in multiple places. Values come
from env (Config.PROFILE_*), comma-separated, so the user can tune the agent
without a code change; sensible defaults cover the documented 12 roles / 15
skills / 4 locations / 0-2 years profile.

Nothing here ever touches network, DB, or secrets — pure config -> list reads.
"""
from config import Config

# --- defaults (used when the matching env var is empty/unset) ---------------

# Target roles — matched as substrings against the job TITLE (lower-cased).
# 12 entries, ordered most-specific first so "QA Automation" wins over "QA".
DEFAULT_ROLES = [
    "python developer",
    "backend developer",
    "backend engineer",
    "software developer",
    "software engineer",
    "full stack developer",
    "react developer",
    "frontend developer",
    "qa tester",
    "qa automation",
    "software tester",
    "data analyst",
]

# Skills — matched against the full title+description text (lower-cased).
# 15 entries: the user's backend / analytics / QA / IT-support stack.
DEFAULT_SKILLS = [
    "python",
    "fastapi",
    "django",
    "rest api",
    "postgresql",
    "mysql",
    "sql",
    "docker",
    "git",
    "power bi",
    "excel",
    "selenium",
    "pytest",
    "jira",
    "linux",
]

# Locations — matched against the job location + description (lower-cased).
# The user's metro/TN coverage (Chennai/Madurai) plus Remote; the analyzer
# treats any of these as a location match.
DEFAULT_LOCATIONS = [
    "chennai",
    "madurai",
    "tamil nadu",
    "remote",
]


def _split(raw: str) -> list:
    """Split a comma-separated env value into a clean lower-cased list."""
    return [
        part.strip().lower()
        for part in (raw or "").split(",")
        if part.strip()
    ]


def _max_experience_years() -> float:
    raw = (getattr(Config, "PROFILE_MAX_EXPERIENCE_YEARS", "") or "2").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 2.0
    # Guard against nonsense (negative / absurd) — keep it parseable.
    return value if 0 <= value <= 50 else 2.0


def roles() -> list:
    """Target job titles (lower-cased substrings)."""
    return _split(getattr(Config, "PROFILE_ROLES", "")) or list(DEFAULT_ROLES)


def skills() -> list:
    """Target skills/technologies (lower-cased substrings)."""
    return _split(getattr(Config, "PROFILE_SKILLS", "")) or list(DEFAULT_SKILLS)


def locations() -> list:
    """Acceptable locations (lower-cased substrings, incl. 'remote')."""
    return _split(getattr(Config, "PROFILE_LOCATIONS", "")) or list(DEFAULT_LOCATIONS)


def max_experience_years() -> float:
    """Upper bound (years) of experience the applicant can claim/attend."""
    return _max_experience_years()


def snapshot() -> dict:
    """Plain dict of the whole profile — handy for logging/tests."""
    return {
        "roles": roles(),
        "skills": skills(),
        "locations": locations(),
        "max_experience_years": max_experience_years(),
    }
