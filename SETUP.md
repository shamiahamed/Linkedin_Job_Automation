# Step-by-Step Setup: Localhost + Cloud

Everything in one order: get **localhost** working first (fast feedback), verify each
feature, then mirror it on the **cloud** (Render + Neon).

---

## PART A — LOCALHOST (your PC)

> Local default DB is `backend/job_automation.db` (SQLite). Emails use your `.env`
> Brevo/Groq keys, so style it as a "test" server until you swap in a throwaway key.

### A1. Install & run
```powershell
cd C:\Users\ahame\Downloads\job-automation\backend
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
pip install pytesseract pillow        # server-side OCR fallback (you already have tesseract .exe)
copy .env.example .env                # then edit with your real keys
uvicorn main:app --reload             # keep this running
```

### A2. Verify server
Open `http://localhost:8000/dashboard`.
Check `http://localhost:8000/health` → `{"status":"ok","app":"Job Auto-Apply"}`.
Dashboard opens the **login screen** — sign in with the local `APP_USERNAME` /
`APP_PASSWORD` from `.env` (if `.env` leaves `APP_PASSWORD` empty = open local dev).

### A3. Load the Chrome extension (one-time)
1. `chrome://extensions` → enable **Developer mode**.
2. **Load unpacked** → pick the `extension` folder in the project.
3. Pin the extension → gear icon → set **Backend URL = `http://localhost:8000`** and
   paste the local API token. Save (stored in `chrome.storage.local`).
   (The extension keeps using the token header; the dashboard itself uses login.)
4. Open `linkedin.com/feed` (or a jobs search) and scroll — captures appear bottom-right.
   Reload extension + F5 after any `content.js` change.

### A4. Test features locally (recommended order)
| # | Test | How |
|---|------|-----|
| 1 | Feed capture | Scroll LinkedIn feed → toast "Captured: …" |
| 2 | Paste capture | Dashboard → paste any job post → **Capture** |
| 3 | Screenshot OCR | Dashboard → choose a screenshot → **Extract** |
| 4 | Confirm flow | Dashboard → ⚙️ Settings → turn **Confirm before sending ON** → capture an email job → it lands in **⚠ To confirm** with a blue card → **Confirm & send** |
| 5 | Resume default | ⚙️ 🗂 Resumes → Upload PDF → **Default** → confirm a job → your default PDF is attached |
| 6 | Resume per job | In the Confirm modal pick a *different* resume for that one job |
| 7 | Phone summary | Capture a job that only has a phone → card shows "phone summary" → email to your inbox |
| 8 | Apply link | Capture a link-only job → "🔗 link" card; the link is emailed to you |
| 9 | Offline queue | DevTools → Network → **Offline** → paste a post → says "Queued offline ✓" → go Online → auto-syncs |
| 10 | Filters | Search title/company; exp 0–1 / 1–3 / 3+; status chips; stats tiles |
| 11 | Dedupe/duplicate | Capture the same post twice → second becomes **duplicate**; "↩ Un-dup" restores it |

### A5. Local gotchas
- Server-side OCR needs the tesseract binary on PATH. The dashboard prefers **on-device
  Tesseract.js** (no install), so this rarely matters.
- The cron job and service worker are cloud-only; locally they no-op.

---

## PART B — CLOUD (Render + Neon)

> Auto-deploys from GitHub `main`. Every `git push` redeploys (~1–2 min for Build →
> Deploy).
>
> **Important (Sep 2026):** Render suspended the old service
> `job-auto-apply-uvi4` for a Google Web Risk false-positive and requires
> "significant code changes" before re-listing. This version replaces the raw
> API-token popup with a **real username/password login** — the upgrade needed to
> re-enable cloud. Create a **new** Web Service from this repo (`backend/` root,
> Docker runtime), set all env vars below, and use the new `https://<name>.onrender.com`.
> Your data is safe in Neon (`DATABASE_URL` unchanged).

### B1. Environment variables — DO THIS (missing now)
Render Dashboard → your service → **Environment** → add:

| Key | Value / source |
|-----|----------------|
| `DATABASE_URL` | your Neon pooled URL (already set) |
| `API_TOKEN` | generated value (already set) — used by the *extension* & scripts |
| `APP_USERNAME` | your dashboard login username (set one) |
| `APP_PASSWORD` | your dashboard login password (set a strong one — DO message) |
| `GROQ_API_KEY` | your key garden at https://console.groq.com (enables AI extraction + cover letters) |
| `GROQ_MODEL` | `openai/gpt-oss-20b` |
| `BREVO_API_KEY` | your Brevo key (enables real email send) |
| `EMAIL_FROM` | the Brevo **verified sender** email |
| `EMAIL_FROM_NAME` | e.g. `Shamim Ahamed J` |
| `YOUR_NAME` | your name |
| `YOUR_PHONE` | your phone |
| `YOUR_EMAIL` | your inbox for phone/link summaries |
| `DEBUG` | `false` |

Save → Render redeploys. `GROQ_API_KEY` / `BREVO_API_KEY` / `EMAIL_FROM` etc. missing =
**why cloud setups email nothing and don't AI-extract**.

### B2. Verify the cloud API (from PowerShell)
```powershell
$H = @{ 'X-API-Key' = '<API_TOKEN>' }
Invoke-RestMethod -Uri 'https://job-auto-apply-uvi4.onrender.com/api/settings' -Headers $H
Invoke-RestMethod -Uri 'https://job-auto-apply-uvi4.onrender.com/api/stats'   -Headers $H
Invoke-RestMethod -Uri 'https://job-auto-apply-uvi4.onrender.com/api/jobs'    -Headers $H
Invoke-RestMethod -Uri 'https://job-auto-apply-uvi4.onrender.com/api/resumes' -Headers $H
```

### B3. Use the cloud dashboard / PWA / mobile
1. Open the app URL on your PC → sign in with `APP_USERNAME` / `APP_PASSWORD`
   (dashboard login; session saved in an HttpOnly cookie).
2. **Phone**: open the URL in Chrome/Edge → browser menu → **Add to Home screen /
   Install app**. It's a standalone PWA now.
3. **Share from LinkedIn app / any app**: install the PWA → in linkedin, open a post →
   Share → choose **Job Auto-Apply** → it opens the dashboard with the text loaded →
   tap **Capture**. (Works offline too — queued and synced later.)
4. **Extension → cloud**: set the extension Backend URL to `https://job-auto-apply-uvi4.onrender.com` + cloud token. Captures go straight to the cloud DB.
5. 🗂 **Resumes → Upload** on the cloud now works; picking **Default** makes that PDF
   attach to every confirm/send. Store everything in the DB (survives redeploys).

### B4. Cloud-specific features
- **Auto-deploy**: any push to `main` redeploys. Watch it at
  `https://dashboard.render.com/web/srv-…/events`.
- **Cron**: `backend/render.yaml` declares a daily cron (`cron_worker.py`) that purges
  no-contact rows >24h and logs a stats line — auto-created when the Blueprint syncs.
- **CI**: `.github/workflows/ci.yml` runs a backend smoke test + extension syntax check
  on every push; watch it at the repo's **Actions** tab.
- **Free-tier cold start**: after ~15 min idle the instance sleeps; the first request
  takes ~30–50s to wake. That's normal.

---

## PART C — THE SAFE "WOW" DEMO

1. In the cloud dashboard: ⚙️ Settings → **Confirm before sending ON** (nothing emails
   anyone without you).
2. Upload your best resume → set **Default**.
3. On your phone, share a LinkedIn post with the PWA → **Capture** → it appears under
   **⚠ To confirm** within seconds.
4. Tap the card → **Confirm & send** → Brevo delivers `Resume + tailored cover letter`
   to the recruiter, status flips to **Applied**.
5. Show the stats tiles, filters, and the offline queue for the full story.

---

## Quick reference

| Action | Local | Cloud |
|--------|-------|-------|
| Dashboard | `http://localhost:8000/dashboard` | `https://<name>.onrender.com/dashboard` |
| Health | `http://localhost:8000/health` | `…/health` |
| Start server | `uvicorn main:app --reload` | push to `main` |
| DB | `backend/job_automation.db` (SQLite) | Neon Postgres (pooled) |
| Extension URL | `http://localhost:8000` | `https://<name>.onrender.com` |
| Extension/scripts | `.env` `API_TOKEN` (header) | Render env `API_TOKEN` (header) |
| Dashboard login | `.env` `APP_USERNAME`/`APP_PASSWORD` | Render env `APP_USERNAME`/`APP_PASSWORD` |
| Resumes | DB (uploaded) + optional `backend/resumes/` | DB only (folder is empty on Render) |