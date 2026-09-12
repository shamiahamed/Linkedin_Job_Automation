"""One-time setup: mint a Gmail API refresh token for THIS project.

Usage (run on your own PC, NOT on Render):
    pip install google-auth-oauthlib google-auth-httplib2
    python gmail_setup.py

It prints the GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET and GMAIL_REFRESH_TOKEN you
must paste into your environment (Render dashboard env vars, or root .env).

Prereqs in Google Cloud Console (console.cloud.google.com):
    1. Enable the Gmail API for the project.
    2. Credentials → Create Credentials → OAuth client ID → Application type
       "Desktop app" → Download JSON as gmail_credentials.json next to this file.
    3. OAuth consent screen → Publishing status → IN PRODUCTION. (Testing mode
       refresh tokens expire after 7 days; production keeps them permanent.)
"""
import json
import os
import sys
from pathlib import Path

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("Missing dependency. Install with:  pip install google-auth-oauthlib")
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
REDIRECT = "http://localhost:8518/"

HERE = Path(__file__).resolve().parent
CRED_FILE = HERE / "gmail_credentials.json"


def main() -> None:
    client_config = None
    if CRED_FILE.exists():
        client_config = json.loads(CRED_FILE.read_text(encoding="utf-8"))
        client_id = client_config.get("installed", {}).get("client_id", "")
        client_secret = client_config.get("installed", {}).get("client_secret", "")
    else:
        client_id = os.getenv("GMAIL_CLIENT_ID", "")
        client_secret = os.getenv("GMAIL_CLIENT_SECRET", "")
        client_config = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": ["http://localhost"],
            }
        }
    if not client_config["installed"]["client_id"]:
        print(
            "No credentials found.\n"
            "  \u2022 Download your OAuth Desktop-client JSON as gmail_credentials.json next to\n"
            "    this file, OR\n"
            "  \u2022 set GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET environment variables."
        )
        sys.exit(1)

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    flow.redirect_uri = REDIRECT
    creds = flow.run_local_server(open_browser=True, port=8518, prompt="consent")

    print("\n=== COPY THESE INTO YOUR ENVIRONMENT (.env / Render) ===\n")
    print("GMAIL_CLIENT_ID=" + client_config["installed"]["client_id"])
    print("GMAIL_CLIENT_SECRET=" + client_config["installed"]["client_secret"])
    print("GMAIL_REFRESH_TOKEN=" + creds.refresh_token)
    print("\n(keep them secret; never commit them)")


if __name__ == "__main__":
    main()