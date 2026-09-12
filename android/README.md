# Job Auto-Detector (Android)

A tiny Android app that watches the LinkedIn app while you scroll and sends each
detected job post to your Job Auto-Apply backend — no extension needed on mobile.

## How it works (Collector → Processor → Intelligence)
```
Android app            FastAPI backend            Groq
(COLLECTOR)            (PROCESSOR)                (INTELLIGENCE)
│                        │                            │
├─ watch LinkedIn app     │                            │
├─ extract screen text   ┐│                            │
├─ debounce events      →│├→ clean / validate / dedupe ─┤
├─ normalize whitespace  │├→ extract job fields (title, │
└─ POST candidate text   ││   company, location, exp,   │
         │               ││   skills, apply info)       │
         ▼               ▼└──────────────────────────────┘
   /api/jobs/from-text             │
                                   ▼
                              Database → Dashboard
```
- The Android app is **only a collector**: it detects screen changes, extracts the
  visible text, debounces, lightly normalizes, sends, and dedupes locally.
- It does **NOT** decide whether the text is a job post. No keyword classifier lives
  on the device — words like "developer"/"hiring" are never used to decide.
- The **backend + Groq** remain the sole authority: non-job candidates come back as
  `422 "Could not recognize a job"` and are silently ignored by the app.
- The backend needs `GROQ_API_KEY` set for classification to work; without it, all
  candidates are rejected. "Enabled" in the app only controls collection.

## Install
1. Download `job-auto-detector.apk` from the **Actions → Build Android APK →
   Artifacts** tab on GitHub (or use the Android Studio build locally).
2. On your phone: allow "Install unknown apps" for your browser/file manager, open
   the APK and install. Not on the Play Store — you sideload it.
3. Open **Job Auto-Detector**, paste:
   - Backend URL: `https://linkedin-job-automation-abhh.onrender.com`
   - API token: your `API_TOKEN`
4. Tap **Save and open Accessibility settings**, find "Job Auto-Apply detector
   (LinkedIn)" and enable it.
5. Scroll LinkedIn. Captured posts appear in your dashboard like any other job.

## Security notes
- The token is stored in the app's private SharedPreferences (restricted storage).
- The Accessibility Service is **package-scoped** to the LinkedIn app only and
  reads/transmits nothing except on-screen text sent to your own backend.
- To stop: turn the switch off, or disable the service in Accessibility settings.

## Privacy
The screen text is only scanned while the LinkedIn app is foregrounded. Everything
captured is sent to your own server, where the Groq model decides whether it is a
job post.