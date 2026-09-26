"""Sending the handover email.

    EmailService              <- interface
        MockEmailService      <- demo: writes a real .eml file instead of sending
        GraphEmailService     <- future Outlook / Microsoft Graph integration (stub)

Only `get_email_service()` decides which one is used (EMAIL_PROVIDER in .env).
"""
from abc import ABC, abstractmethod
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from backend import config


class EmailService(ABC):
    @abstractmethod
    def send(self, subject: str, html_body: str, text_body: str) -> dict:
        """Send the email and return a small status dict."""


class MockEmailService(EmailService):
    """Simulates sending. The email is saved as an .eml file you can open in Outlook."""

    def __init__(self, out_dir: Path | None = None):
        self.out_dir = out_dir or config.SENT_EMAIL_DIR

    def send(self, subject: str, html_body: str, text_body: str) -> dict:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = config.EMAIL_FROM
        msg["To"] = f"{config.EMAIL_TO_NAME} <{config.EMAIL_TO}>"
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")

        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"sent_{datetime.now():%Y%m%d_%H%M%S}.eml"
        path.write_bytes(bytes(msg))
        return {
            "sent": True,
            "provider": "mock",
            "recipients": config.EMAIL_TO_NAME,
            "address": config.EMAIL_TO,
            "file": config.display_path(path),
        }


class GraphEmailService(EmailService):
    """Future integration point - Outlook via Microsoft Graph (not used in the demo).

    How it would work:
        1. Register an app in Microsoft Entra ID with the Mail.Send permission.
        2. Read GRAPH_TENANT_ID / GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET from environment
           variables or a secret store - never hard-code or commit them.
        3. Get a token with `msal.ConfidentialClientApplication(...).acquire_token_for_client(...)`.
        4. POST https://graph.microsoft.com/v1.0/users/{sender}/sendMail with
           {"message": {"subject": ..., "body": {"contentType": "HTML", "content": html_body},
                        "toRecipients": [{"emailAddress": {"address": config.EMAIL_TO}}]}}
    """

    def send(self, subject: str, html_body: str, text_body: str) -> dict:
        raise NotImplementedError("Microsoft Graph sending is not configured in this demo.")


def get_email_service() -> EmailService:
    if config.EMAIL_PROVIDER == "graph":
        return GraphEmailService()
    return MockEmailService()
