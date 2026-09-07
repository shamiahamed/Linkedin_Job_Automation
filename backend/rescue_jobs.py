"""
One-off rescue: re-extract title/company/location/experience/contacts for poorly
captured rows using the Groq LLM. NEVER sends email, NEVER deletes rows, NEVER
touches the applications table. Only UPDATES fields when the LLM finds a clear
improvement. Verbose per-row report.
Usage:  python rescue_jobs.py            (dry-run, prints what would change)
        python rescue_jobs.py --apply    (write changes)
"""
import sqlite3
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from routes.jobs import _title_weak, _company_ok
from services import llm

DB = Path(__file__).resolve().parent / "job_automation.db"


def _extract_with_retry(text, attempts=3, pause=4):
    for i in range(attempts):
        ref = llm.extract_job(text)
        if ref:
            return ref, None
        time.sleep(pause * (i + 1))
    return {}, "rate-limited/no-result after retries"


def _as_list(raw):
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except Exception:
        return str(raw).split(",")


def _ascii(s):
    return (str(s) or "").encode("ascii", "replace").decode()


def main():
    dry = "--apply" not in sys.argv
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id,title,company,location,experience,salary,description,emails,phones,url,apply_link,status,created_at "
        "FROM jobs ORDER BY id"
    ).fetchall()
    if not llm._enabled():
        print("GROQ key not present in .env — nothing to rescue. Add GROQ_API_KEY and restart.")
        return

    changed = skipped = 0
    for r in rows:
        try:
            title_junk = _title_weak(r["title"])
            co_junk = bool(r["company"]) and not _company_ok(r["company"])
            if not (title_junk or co_junk):
                skipped += 1
                continue
            text = " ".join(str(x or "") for x in [r["description"], r["title"], r["company"], r["location"]])
            if not text.strip():
                skipped += 1
                continue
            ref, err = _extract_with_retry(text)
            if not ref:
                print(f"[{r['id']:>3}] no LLM result ({err}) — kept")
                continue
            upd = {}
            r_title = str(ref.get("title") or "").strip()
            if title_junk and r_title and not _title_weak(r_title) and len(r_title) >= 3:
                upd["title"] = r_title
            new_co = str(ref.get("company") or "").strip()
            if (co_junk or not r["company"]) and new_co and _company_ok(new_co):
                upd["company"] = new_co
            if str(ref.get("location") or "").strip() and not r["location"]:
                upd["location"] = str(ref["location"]).strip()[:80]
            if str(ref.get("experience") or "").strip() and not r["experience"]:
                upd["experience"] = str(ref["experience"]).strip()[:40]
            if str(ref.get("salary") or "").strip() and not r["salary"]:
                upd["salary"] = str(ref["salary"]).strip()[:60]
            new_emails = sorted(set(_as_list(r["emails"])) | set(ref.get("emails") or []))
            if new_emails:
                upd["emails"] = json.dumps(new_emails)
            new_phones = sorted(set(_as_list(r["phones"])) | set(ref.get("phones") or []))
            if new_phones:
                upd["phones"] = json.dumps(new_phones)
            if not r["apply_link"] and str(ref.get("apply_link") or "").strip():
                upd["apply_link"] = str(ref["apply_link"]).strip()[:300]
            if not upd:
                print(f"[{r['id']:>3}] no improvement found (kept) — {_ascii(r['title'])}")
                continue
            print(_ascii(f"[{r['id']:>3}] {r['title']} | {r['company']}  ->  {upd.get('title', r['title'])} | {upd.get('company', r['company'])}"))
            changed += 1
            if not dry:
                sets = ", ".join(f"{k}=?" for k in upd)
                db.execute(f"UPDATE jobs SET {sets} WHERE id=?", list(upd.values()) + [r["id"]])
                db.commit()
        except Exception as e:
            print(f"[{r['id']:>3}] ERROR {_ascii(e)} — continuing")
    if not dry:
        db.commit()
    print(f"\nrescue {'DRY-RUN' if dry else 'APPLIED'}: {changed} rows updated, {skipped} skipped")
    if dry:
        print("Run with --apply to write the changes (no emails are ever sent).")


if __name__ == "__main__":
    main()