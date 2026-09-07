"""Targeted fix pass: LLM-rescue remaining weak rows, apply known-correct
values from the user's pasted feed for a few image posts, and strip leftover
emoji / 'Eligibility:' junk from recorded titles. No emails, no deletes."""
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from routes.jobs import _title_weak, _company_ok
from services import llm

DB = Path(__file__).resolve().parent / "job_automation.db"


def _ascii(s):
    return (str(s) or "").encode("ascii", "replace").decode()


def _as_list(raw):
    try:
        v = json.loads(raw) if raw else []
        return list(v) if isinstance(v, list) else []
    except Exception:
        return str(raw or "").split(",")


def clean_title(t):
    t = re.sub(r"[\U0001F300-\U0001FAFF]", "", t or "")
    t = re.split(r"\s+(?:Eligibility|Experience|Salary)\s*:", t)[0]
    t = re.sub(r"^[\s\-–—:]+|[\s\-–—:]+$", "", t).strip()
    return t


def llm_extract(text, tries=3):
    for i in range(tries):
        r = llm.extract_job(text)
        if r:
            return r
        time.sleep(3 * (i + 1))
    return {}


def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    # 1) LLM rescue for the stubborn image/placeholder-only rows
    for rid in (68, 71, 72, 75, 78):
        r = db.execute("SELECT * FROM jobs WHERE id=?", (rid,)).fetchone()
        if not r:
            continue
        text = " ".join(str(x or "") for x in [r["description"], r["company"], r["location"]])
        if not text.strip():
            print(f"[{rid:>3}] no text to extract from - kept")
            continue
        ref = llm_extract(text)
        if not ref:
            print(f"[{rid:>3}] LLM no result - kept")
            continue
        upd = {}
        nt = clean_title(ref.get("title"))
        if _title_weak(r["title"]) and nt:
            upd["title"] = nt
        nc = str(ref.get("company") or "").strip()
        if nc and _company_ok(nc) and not _company_ok(r["company"]):
            upd["company"] = nc
        if not r["location"] and str(ref.get("location") or "").strip():
            upd["location"] = str(ref["location"]).strip()[:80]
        if not r["experience"] and str(ref.get("experience") or "").strip():
            upd["experience"] = str(ref["experience"]).strip()[:40]
        if not r["apply_link"] and str(ref.get("apply_link") or "").strip():
            upd["apply_link"] = str(ref["apply_link"]).strip()[:300]
        new_emails = sorted(set(_as_list(r["emails"])) | set(ref.get("emails") or []))
        if new_emails:
            upd["emails"] = json.dumps(new_emails)
        new_phones = sorted(set(_as_list(r["phones"])) | set(ref.get("phones") or []))
        if new_phones:
            upd["phones"] = json.dumps(new_phones)
        if not upd:
            print(f"[{rid:>3}] no improvement - kept")
            continue
        sets = ", ".join(f"{k}=?" for k in upd)
        db.execute(f"UPDATE jobs SET {sets} WHERE id=?", list(upd.values()) + [rid])
        db.commit()
        print(_ascii(f"[{rid:>3}] LLM -> {upd.get('title', r['title'])} | {upd.get('company', r['company'])}"))

    # 2) Known-correct values (from the user's pasted feed / verified posts)
    known = {
        61: {"title": "Physical Design Engineer",
             "company": "VALSOC Semiconductor Pvt Ltd",
             "experience": "0-3 Years"},
        63: {"title": "Associate Engineer - Software", "company": "Qualcomm", "experience": "Fresher"},
        65: {"title": "Office Admin", "company": "Clarisco Solutions Pvt Ltd"},
        66: {"title": "Web Developer - Fresher"},
        74: {"title": "Associate Engineer", "experience": "Fresher"},
        79: {"title": "Intern - LYTIVA"},
        81: {"title": "SAP MM Fresher"},
    }
    for rid, upd in known.items():
        r = db.execute("SELECT * FROM jobs WHERE id=?", (rid,)).fetchone()
        if not r:
            continue
        sets = ", ".join(f"{k}=?" for k in upd)
        db.execute(f"UPDATE jobs SET {sets} WHERE id=?", list(upd.values()) + [rid])
        db.commit()
        print(_ascii(f"[{rid:>3}] KNOWN -> {upd.get('title', r['title'])} | {upd.get('company', r['company'])}"))

    # 3) Generic status sanity: strip emoji / Eligibility tails from every title
    scan = db.execute("SELECT id,title FROM jobs").fetchall()
    for r in scan:
        nt = clean_title(r["title"])
        if nt and nt != r["title"] and nt.lower() != "job opening":
            db.execute("UPDATE jobs SET title=? WHERE id=?", (nt, r["id"]))
            db.commit()
            print(_ascii(f"[{r['id']:>3}] TITLE-CLEAN: '{r['title']}' -> '{nt}'"))

    print("\nfix pass complete.")


if __name__ == "__main__":
    main()