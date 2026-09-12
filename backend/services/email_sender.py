"""Send application emails through the Gmail REST API (OAuth2).

Why not SMTP? Render's free tier blocks outbound SMTP ports (25/465/587), so we
use Gmail's HTTPS API instead — port 443 is always open. Emails are sent from the
authenticated Gmail account itself, so the From header is the real address and
there is no "via <relay-domain>" labelling.

One-time setup (see gmail_setup.py):
  1. Google Cloud: enable the Gmail API, create OAuth Client ID (Desktop app).
  2. Run `python gmail_setup.py` → grants consent → prints a refresh token.
  3. Put GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN in env
     (Render dashboard or local .env).
"""
import base64
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config import Config

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


class EmailSender:
    def __init__(self):
        self.client_id = Config.GMAIL_CLIENT_ID
        self.client_secret = Config.GMAIL_CLIENT_SECRET
        self.refresh_token = Config.GMAIL_REFRESH_TOKEN
        self.sender_email = Config.GMAIL_USER or Config.EMAIL_FROM
        self.sender_name = Config.EMAIL_FROM_NAME or "Shamim Ahamed J"
        self.configured = bool(self.client_id and self.client_secret and self.refresh_token)
        self._service = None

    def _service_instance(self):
        creds = Credentials(
            token=None,
            refresh_token=self.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=SCOPES,
        )
        creds.refresh(Request())
        return build("gmail", "v1", credentials=creds)

    def _decode_attachment(self, att):
        content = att.get("content") or b""
        try:
            if isinstance(content, bytes):
                return content
            return base64.b64decode(content)
        except Exception:
            return str(content).encode()

    def send_email(self, to_email: str, subject: str, html_content: str,
                   to_name: str = "", attachments: list = None) -> dict:
        """Send an application email via the Gmail API, optionally with attachments."""
        if not self.configured:
            raise RuntimeError(
                "Gmail API not configured. Set GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET "
                "and GMAIL_REFRESH_TOKEN in the environment (see .env.example)."
            )
        if not self._service:
            self._service = self._service_instance()

        msg = MIMEMultipart("alternative")
        msg["From"] = f"{self.sender_name} <{self.sender_email}>"
        msg["To"] = to_email if not to_name else f"{to_name} <{to_email}>"
        msg["Subject"] = subject
        msg["Reply-To"] = self.sender_email
        msg.attach(MIMEText(html_content, "html"))
        for att in attachments or []:
            part = MIMEApplication(self._decode_attachment(att), _subtype="pdf")
            part.add_header("Content-Disposition", "attachment",
                            filename=att.get("name", "resume.pdf"))
            msg.attach(part)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        try:
            result = (
                self._service.users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )
            return {"success": True, "message_id": result.get("id", "")}
        except Exception as e:
            return {"success": False, "error": f"Gmail API error: {e}"}