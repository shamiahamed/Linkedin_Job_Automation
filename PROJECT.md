# Job Auto-Apply — Project Overview

An automated **LinkedIn job capture and application assistant** with a Chrome extension (desktop feed detection + OCR), a **FastAPI** backend, **Gmail API** transactional email, **Neon PostgreSQL**, served by **Render**, and a **mobile-first PWA dashboard** that can be installed as an APK via a Trusted Web Activity (TWA) wrapper.

The system watches a LinkedIn feed for job posts, extracts role/company/experience/contact/apply-link, optionally emails an application (or stages it for your approval), emails you phone-call summaries and open-apply-link reminders, and keeps a searchable, filterable queue.

---

## Architecture

```
 Chrome extension        Mobile (any browser)         Linkedin Android app
 (feed auto-detect)      (paste / share-text / OCR)   (share menu / PWA)
        │                       │                            │
        └──────────────┬────────┴─────────────┬──────────────┘
                       ▼                      ▼
            POST /api/jobs            POST /api/jobs/from-text
            (linkedin source)         (mobile / ocr_mobile / mobile_offline)
                       │             POST /api/ocr/upload (server Tesseract fallback)
                       ▼
             FastAPI + SQLAlchemy (Render)
        ┌──────────────┼───────────────┬───────────────┐
        ▼              ▼               ▼               ▼
    Neon Postgres   Groq LLM         Gmail API      Resumes (DB)
    (jobs/apps)     (extraction +    (email +         (PDF management,
                    cover letters)    attachments)     per-job selection)
```

### Capture paths
1. **Desktop** — the Chrome extension (`extension/content.js`) walks the LinkedIn feed
   (`/jobs` and `/feed`) with a MutationObserver + debounce, extracts the job, and
   POSTs it. Company always comes from LinkedIn's own author metadata
   ("Hide post by …" aria-label) — never hardcoded or regex-guessed.
2. **Mobile paste** — any browser: copy the post → "Capture" → LLM extraction.
3. **Mobile screenshot** — OCR on-device via **Tesseract.js** in the browser, then the
   same AI extraction path. Server-side Tesseract is the fallback.
4. **Mobile share menu** — installing the PWA adds a `share_target`; sharing a post
   opens the dashboard with the text preloaded for instant capture. Offline shares
   are queued in **IndexedDB** and synced automatically when the connection returns.
5. **LinkedIn Android app** — in-app detection is impossible from the web, but the
   same share-menu path works from the app. (Optional future: a native Accessibility
   Service app that reads the screen.)

### Decision flow on capture (`backend/routes/jobs.py::_auto_apply_if_enabled`)
```
no email/phone/link  ──► no_contact  (auto-deleted after 24 h unless it has an apply link)
apply link only      ──► apply link emailed to YOUR inbox  →  link_email_sent
phone present        ──► call summary emailed to YOUR inbox →  phone_summary_sent
email present & confident & fresher-eligible (≤1 yr) & confirm OFF
                     ──► application emailed automatically →  applied
email present & confirm ON
                     ──► staged →  ready_to_send  (you approve in the dashboard)
unconfident capture  ──► pending  (manual review; never auto-emailed)
already emailed      ──► duplicate (never emailed twice without explicit confirm)
```

## Job statuses
| Status              | Meaning                                                            |
|---------------------|--------------------------------------------------------------------|
| `pending`           | Needs manual review (unconfident or above your experience bar).     |
| `ready_to_send`     | Confirm-before-send ON: staged with an email, awaiting your approval.|
| `applied`           | Application email sent (or confirmed from the queue).               |
| `phone_summary_sent`| Call-summary emailed to you so you can call the number.             |
| `link_email_sent`   | Apply-link emailed to you so you can open and apply it yourself.    |
| `duplicate`         | Same contact email already got an application (verify, then un-dup). |
| `no_contact`        | No email/phone/apply link — auto-deleted after 24 h.                |

## Confirm-before-send
Toggle in **Settings**. When **ON**, no recruiter-facing email is ever sent
automatically — email jobs land in the queue as `ready_to_send`, and you press
**"Confirm & send"** (optionally picking a specific resume from your library) in the
dashboard or approve the in-page card the extension shows. This is the safe default
for a public demo / "wow" walkthrough.

## Resumes
- **Dashboard → 🗂 Resumes**: upload PDFs (stored base64 in the DB, so they survive
  Render's stateless file system), set a default, delete. Pick one per job inside the
  Confirm modal.
- Folder auto-selection (`backend/services/resume_selector.py`) still works as a
  fallback when no upload is chosen and `backend/resumes/` is populated.

## API (token OR login session)
Routes authenticate via the `X-API-Key` / Bearer `API_TOKEN` header (extension,
scripts) **or** the dashboard's login session cookie. Public: `/health`,
`/api/auth/login`, `/api/auth/me`.
```
POST /api/auth/login      username+password -> HttpOnly session cookie
POST /api/auth/logout     clears the session
GET  /api/auth/me        {authenticated:true|false}
GET  /api/jobs[?status=…&exp=0-1|1-3|3+&source=…&q=…&since=…]   list + filter
POST /api/jobs                    extension capture
POST /api/jobs/from-text          AI extraction from pasted/shared/OCR text
PATCH /api/jobs/{id}              change status (e.g. reject confirm → pending)
POST /api/jobs/{id}/apply         send now (email or phone summary)
POST /api/jobs/{id}/confirm       approve a ready_to_send job (email send)
DELETE /api/jobs/{id}             delete job + its applications
GET/PUT /api/settings             auto_apply + confirm_before_send
GET  /api/applications        PUT /api/applications/{id}      application log
GET  /api/stats                   counts per status
GET/POST/DELETE /api/resumes                  upload/CI resume library
POST /api/ocr/upload              server-side OCR fallback
GET  /api/ocr/status              health of server Tesseract
POST /api/debug/log  GET /api/debug/logs     diagnostic trail
GET  /health                      public readiness probe
```

## Deployment (Render + Neon)
- **Web service**: `backend` (Docker). Dockerfile pins `WEB_CONCURRENCY=-1` (one
  gunicorn worker — the free 0.1 CPU / 512 MB plan can't OCR and serve two workers).
  Auto-deploys from the `main` branch (GitHub → Render).
- **Auto-cleanup**: inside the app — startup loop runs every 6h plus on every job
  ingest (drops stale no-contact rows >24h, auto-purges captured jobs after 5 days).
- **DB**: Neon Postgres (pooler connection). Tables are created on startup with retry
  so the free-tier cold start never crashes a worker.
- **Env vars** (set in the Render dashboard — the Blueprint marks them `sync: false`:
  `DATABASE_URL`, `API_TOKEN`, `GROQ_API_KEY`, `GROQ_MODEL`, `GMAIL_CLIENT_ID`,
  `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `GMAIL_USER`, `EMAIL_FROM`,
  `EMAIL_FROM_NAME`, `YOUR_NAME`, `YOUR_PHONE`, `YOUR_EMAIL`,
  `APP_USERNAME`, `APP_PASSWORD`, `DEBUG`.
  Without `GROQ_API_KEY` AI extraction and cover letters are disabled; without the
  `GMAIL_*` vars the application emails can't be sent (guarded error message).
- **Auth**: the dashboard is a personal login page (`APP_USERNAME`/
  `APP_PASSWORD` → signed HttpOnly session cookie). No raw API-token popup — that
  prompt is the phishing-signal Google Web Risk flagged on the old free subdomain.

## Local development
```
cd backend
python -m venv venv && .\venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # fill in keys
uvicorn main:app --reload   # http://localhost:8000/dashboard
```
Load the extension from `extension/` via `chrome://extensions` → Developer mode →
Load unpacked. One-time setup: click the extension → set backend URL `http://localhost:8000`
and the API token (extension uses the token header; the dashboard uses username/password
login). After any `extension/content.js` change: reload the extension and
refresh LinkedIn (F5).

## Feature roadmap
- [x] Feed auto-detect (desktop) + screenshot OCR
- [x] Email / phone / apply-link auto-actions with safety gates + dedupe
- [x] Mobile paste + on-device OCR + share-menu capture (PWA)
- [x] Confirm-before-send (dashboard queue + extension card) — none of your work
      interviews are ever emailed automatically
- [x] Resume library (DB-backed uploads, default + per-job selection)
- [x] Offline capture queue (IndexedDB → online sync)
- [x] Installable PWA + share target (ready for TWA → APK)
- [x] Render cron housekeeping + GitHub Actions CI
- [ ] Native Accessibility Service (screen-reading capture inside the LinkedIn app)
- [ ] TWA APK build (pwabuilder / Bubblewrap) for Google Play sideload

## Demo / wow storyline
1. Paste or share a LinkedIn post from your phone → it appears in the queue.
2. Open the PWA → tap "Confirm & send" → your resume + tailored cover letter go to the
   recruiter's email via the Gmail API.
3. Show the stats ("Applied", phone summaries, link reminders) and the offline queue.
4. Share the deploy URL; a visitor pastes a fake post and watches extraction run.