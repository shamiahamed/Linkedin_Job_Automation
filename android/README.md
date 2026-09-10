# Job Auto-Detector (Android)

A tiny Android app that watches the LinkedIn app while you scroll and sends each
detected job post to your Job Auto-Apply backend — no extension needed on mobile.

## How it works
- An **Accessibility Service** (scoped to `com.linkedin.android`) reads the on-screen
  text while the LinkedIn app is open.
- When the visible text looks like a job post (job keywords + a LinkedIn post URL),
  it POSTs the text to your backend:
  `{backend_url}/api/jobs/from-text` with header `X-API-Key: <your API_TOKEN>`.
- Deduping is built in (each post URL / snippet is sent once).

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
  reads/transmits nothing except the post text sent to your own backend.
- To stop: turn the switch off, or disable the service in Accessibility settings.

## Privacy
The screen text is only scanned while the LinkedIn app is foregrounded, and only
text matching job-post heuristics is sent — to your own server.