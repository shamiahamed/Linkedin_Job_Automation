"""
Google RSS job source (second external feed, deterministic — NO LLM).

Explained in one block: fetches RSS feeds (built from configurable query/
location searches, or taken verbatim from GOOGLE_RSS_FEED_URLS), parses them
with the stdlib ElementTree, and normalizes every entry into the exact
JobCreate-shaped dict the rest of the app uses — the same keys Adzuna's
`to_job_dict` produces. Jobs are stamped `source="auto_fetch"` so they land in
the existing Recent view and go through the SAME ingest_job() pipeline in
routes/jobs.py (dedupe, insertion, auto-apply, push, retention). No ingest
logic lives here.

Guarantees:
  * deterministic — pure parsing + regex, no LLM
  * never raises — a malformed/empty/inaccessible feed yields [] (or a
    per-feed skip), so a failing source can never crash the app
  * company/location may be absent in the feed and are preserved as "" without
    error (missing-company is an expected, handled case)
"""
import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import requests

from config import Config
from services.job_parser import JobParser
from services.job_search import experience_label, is_senior_role

logger = logging.getLogger("uvicorn.error")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Initial query/location pairs (per spec). Overridable via GOOGLE_RSS_SEARCHES
# in env ("query|location,..."). Used when GOOGLE_RSS_FEED_URLS is empty.
DEFAULT_SEARCHES = [
    ("react developer", "Chennai"),
    ("python developer", "Chennai"),
    ("qa tester", "Chennai"),
    ("technical support", "Chennai"),
    ("software developer", "Chennai"),
    ("walk-in interview", "Chennai"),
    ("walk-in interview", "Madurai"),
    ("react developer", "Madurai"),
    ("python developer", "Madurai"),
    ("software freshers", "Tamil Nadu"),
    ("freshers hiring", "Tamil Nadu"),
]

_DESC_MAX = 4000


def _local(tag: str) -> str:
    """Tag local name without any XML namespace prefix."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _clean_html(text: str) -> str:
    """Strip tags, unescape entities, collapse whitespace (deterministic)."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return " ".join(text.split())


def _find_local(parent, name):
    for child in parent:
        if _local(child.tag) == name:
            return child
    return None


def _find_all_local(parent, name):
    return [child for child in parent if _local(child.tag) == name]


def _extract_rss_item(item) -> dict:
    out = {"title": "", "link": "", "guid": "", "description": "",
           "published_at": "", "company": ""}
    for child in item:
        tag = _local(child.tag).lower()  # RSS uses "pubDate" (camelCase)
        if tag == "title":
            out["title"] = _clean_html(child.text)
        elif tag == "link":
            out["link"] = _clean_html(child.text)
        elif tag == "guid":
            out["guid"] = _clean_html(child.text)
        elif tag == "description":
            out["description"] = _clean_html(child.text)
        elif tag in ("pubdate", "published", "updated", "date"):
            out["published_at"] = _clean_html(child.text)
        elif tag == "creator":
            out["company"] = _clean_html(child.text)
        elif tag == "author":
            name = _find_local(child, "name")
            value = _clean_html(name.text) if name is not None else _clean_html(child.text)
            if value:
                out["company"] = value
    return out


def _extract_atom_entry(entry) -> dict:
    out = {}
    for child in entry:
        tag = _local(child.tag)
        if tag == "title":
            out["title"] = _clean_html(child.text)
        elif tag == "id":
            out["guid"] = _clean_html(child.text)
        elif tag == "link":
            out["link"] = _clean_html(child.get("href")) or out.get("link", "")
        elif tag in ("summary", "content"):
            out["description"] = _clean_html(child.text)
        elif tag in ("published", "updated"):
            out["published_at"] = _clean_html(child.text or child.get("datetime"))
        elif tag == "author":
            name = _find_local(child, "name")
            value = _clean_html(name.text) if name is not None else ""
            if value:
                out.setdefault("company", value)
    out["title"] = out.get("title") or out.get("description", "")[:120]
    return out


def parse_rss(xml_text) -> list:
    """Deterministically parse RSS 2.0 (and basic Atom) XML into item dicts.

    Returns a list of dicts with keys: title, link, guid, description,
    published_at, company. Any parse failure returns [] — never raises.
    """
    if not xml_text or not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    root_name = _local(root.tag)
    if root_name == "feed":  # Atom
        return [_extract_atom_entry(e) for e in _find_all_local(root, "entry")]

    if root_name == "rss":
        channel = _find_local(root, "channel")
    else:
        channel = _find_local(root, "channel") or root
    if channel is None:
        return []

    items = []
    for item in _find_all_local(channel, "item"):
        parsed = _extract_rss_item(item)
        if parsed:
            items.append(parsed)
    return items


def to_job_dict(item: dict, location: str = "") -> dict:
    """Normalize one RSS item into the app's JobCreate shape (same keys and
    semantics as Adzuna's to_job_dict). `source` stays "auto_fetch" so fetched
    jobs share the Recent view, purge/retention and auto-apply rules."""
    title = _clean_html(item.get("title") or "")
    url = (_clean_html(item.get("link") or "") or _clean_html(item.get("guid") or "")).strip()
    desc = _clean_html(item.get("description") or "")[:_DESC_MAX]
    company = _clean_html(item.get("company") or "")
    loc = _clean_html(item.get("location") or "") or _clean_html(location)
    return {
        "title": title,
        "company": company,
        "location": loc,
        "url": url,
        "description": desc,
        "emails": [],
        "phones": [],
        "experience": experience_label(title, desc),
        "salary": "",
        "source": "auto_fetch",
        "apply_link": url,
        # Published date is preserved in the item (not a DB column yet) so
        # callers can filter/sort by feed freshness without re-parsing.
        "published_at": _clean_html(item.get("published_at") or ""),
    }


def configured_searches() -> list:
    """(query, location) pairs from GOOGLE_RSS_SEARCHES, or the defaults."""
    raw = (Config.GOOGLE_RSS_SEARCHES or "").strip()
    pairs = []
    if raw:
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "|" in pair:
                query, _, location = pair.partition("|")
                pairs.append((query.strip(), location.strip()))
            else:
                pairs.append((pair.strip(), ""))
    return pairs or list(DEFAULT_SEARCHES)


def _explicit_urls() -> list:
    return [u.strip() for u in (Config.GOOGLE_RSS_FEED_URLS or "").split(",") if u.strip()]


def _enabled() -> bool:
    return str(Config.GOOGLE_RSS_ENABLED).strip().lower() not in ("0", "false", "no", "off", "")


def build_urls(searches=None) -> list:
    """URLs to fetch: explicit feeds, else template-format from searches."""
    explicit = _explicit_urls()
    if explicit:
        return explicit
    template = Config.GOOGLE_RSS_URL_TEMPLATE
    urls = []
    for query, location in (searches if searches is not None else configured_searches()):
        query = (query or "").strip()
        if not query:
            continue
        try:
            urls.append(
                template.replace("{query}", quote_plus(query))
                .replace("{location}", quote_plus(location))
            )
        except Exception:
            continue
    return urls


def fetch_one(url: str, timeout: float = 20.0):
    """GET one feed. Returns response text or None — never raises."""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning("google rss fetch failed: %s (%s)", url, e)
        return None


def fetch_google_rss_jobs(searches=None, urls=None, limit: int = 200, max_seconds: float = 45.0) -> list:
    """Fetch + parse all configured RSS feeds and normalize every entry.

    Live URLs come from build_urls(searches) unless `urls` is passed directly.
    Per-feeds: a network failure, a 500, or malformed XML just skips that feed.
    Global: a run always finishes (time budget), favorite `is_senior_role`
    filtering, and in-run duplicate pruning by (title, url, company) URL-count.
    Returns JobCreate-shaped dicts (source="auto_fetch"). Never raises.
    """
    if not _enabled():
        return []
    candidates = urls if urls is not None else build_urls(searches)
    if not candidates:
        return []

    jobs = []
    seen = set()
    deadline = time.monotonic() + max_seconds
    for url in candidates:
        if time.monotonic() > deadline:
            break
        try:
            text = fetch_one(url)
            if not text:
                continue
            for item in parse_rss(text):
                job = to_job_dict(item)
                if not job["title"] or not job["url"]:
                    continue
                if is_senior_role(job["title"], job["description"]):
                    continue
                key = (job["title"].lower(), job["url"], job["company"].lower())
                if key in seen:
                    continue
                seen.add(key)
                jobs.append(job)
                if len(jobs) >= limit:
                    return jobs
        except Exception as e:  # noqa: BLE001 — one bad feed must not kill the run
            logger.warning("google rss feed %s failed: %s", url, e)
            continue
    return jobs