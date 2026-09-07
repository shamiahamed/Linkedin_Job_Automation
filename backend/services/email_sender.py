"""
Email sending via Brevo API (SendInBlue).
Uses the sib-api-v3-sdk package.
"""
import os
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from config import Config


class EmailSender:
    def __init__(self):
        self.api_key = Config.BREVO_API_KEY
        self.sender_email = Config.BREVO_SENDER_EMAIL
        self.sender_name = Config.BREVO_SENDER_NAME
        self.configured = bool(self.api_key)
        self.api_instance = None

    def configure(self):
        configuration = sib_api_v3_sdk.Configuration()
        configuration.api_key["api-key"] = self.api_key
        self.api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
            sib_api_v3_sdk.ApiClient(configuration)
        )

    def send_email(self, to_email: str, subject: str, html_content: str,
                   to_name: str = "", attachments: list = None) -> dict:
        """Send a transactional email via Brevo, optionally with attachments."""
        if not self.configured:
            raise RuntimeError("Brevo API key not configured. Set BREVO_API_KEY in .env")

        if not self.api_instance:
            self.configure()

        sender = {"email": self.sender_email, "name": self.sender_name}
        to = [{"email": to_email, "name": to_name or to_email}]

        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            sender=sender,
            to=to,
            subject=subject,
            html_content=html_content,
            attachment=attachments or None,
        )

        try:
            api_response = self.api_instance.send_transac_email(send_smtp_email)
            return {"success": True, "message_id": api_response.message_id}
        except ApiException as e:
            return {
                "success": False,
                "error": f"Brevo API error {e.status}: {e.body}",
            }
