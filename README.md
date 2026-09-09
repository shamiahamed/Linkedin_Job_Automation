# Job Auto-Apply — Deploy & Mobile

Capture LinkedIn job posts → extract contacts (with Groq + OCR) → auto-apply by
email → dashboard. The backend + dashboard now run anywhere and are usable as an
installable PWA on your phone.

## Deploy to Render (free)

1. Push this repo to GitHub (backend/ is the deploy root — the `Dockerfile`,
   `requirements.txt` and `render.yaml` live there).
2. In Render, use **Blueprint** (Render will read `backend/render.yaml`) or:
   - **New → Web Service**, connect repo, Root Directory: `backend`,
     Runtime: **Docker**.
3. Render auto-creates a **free Postgres** DB and a random **API_TOKEN**
   (`generateValue: true`). In **Environment → Edit** set these (see .env.example):
   - `GROQ_API_KEY` (from console.groq.com — enables AI extraction + email drafts)
   - `BREVO_API_KEY`, `EMAIL_FROM`, `EMAIL_FROM_NAME` (email sending)
   - `YOUR_NAME`, `YOUR_PHONE`, `YOUR_EMAIL` (your details)
   - `APP_USERNAME`, `APP_PASSWORD` (dashboard login — personal credentials)
   - Leave `DATABASE_URL` and `API_TOKEN` as Render generated them.
4. First deploy takes a few minutes (Tesseract install). Watch Build logs.
5. You get `https://<your-app>.onrender.com`.

> **Note:** For an always-on instance (no cold-start sleep) you'll need a paid
> plan. On the free plan the service sleeps after 15 min idle and takes ~50s to
> wake — the extension's own retry/two-pass capture survives that, but monthly
> requests are capped (750 hrs) on free.

## Connect the Chrome extension

1. `chrome://extensions` → the extension → **Reload**, then open it.
2. Click **⚙️ Settings**, set Backend URL to `https://<your-app>.onrender.com`
   and paste the `API_TOKEN` from Render (the extension still uses the token
   header; the dashboard itself uses username + password login).
3. Reload (F5) your LinkedIn feed tab. New posts now go to the cloud, and the
   dashboard works from any device.

## Use the mobile PWA

1. On your phone open `https://<your-app>.onrender.com/dashboard` in
   Chrome/Safari.
2. Sign in with your `APP_USERNAME` / `APP_PASSWORD` (the dashboard is a
   personal, password-protected login — no raw API token prompt).
3. **Install to home screen** (Chrome: menu → Add to home screen; Safari:
   Share → Add to Home Screen) → opens full-screen like an app.
4. **Paste & Capture:** copy a job post from LinkedIn, tap **📋 Paste a LinkedIn
   Post**, paste, **Capture This Post**. Groq extracts role/company/contact and
   the normal gate/dedupe/auto-apply flow runs — no LinkedIn app or extension
   needed on mobile.

## Local development (unchanged)

```bash
cd backend
pip install -r requirements.txt
python main.py            # API + dashboard at http://localhost:8000
```

Local dev stays open by default (empty `API_TOKEN` and empty `APP_PASSWORD`).
Set `APP_USERNAME`/`APP_PASSWORD` in `.env` to enable the dashboard login.

## API

All `/api/*` routes authenticate either with the token header
(`X-API-Key: <token>` / `Authorization: Bearer <token>`) for scripts and the
extension, **or** with the dashboard's login session cookie. Public:
`/api/health`, `/api/auth/login`, `/api/auth/me`.

- `POST /api/jobs/from-text` `{text}` — paste-capture (mobile).
- `POST /api/jobs` — extension capture.
- `GET/POST /api/jobs/{id}/apply`, `/api/settings`, `/api/stats`,
  `/api/ocr/*`, `/api/applications` — as before.
