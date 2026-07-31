import logging
import smtplib
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, html_body: str) -> None:
    """Send an email, or log it if SMTP isn't configured.

    Local dev has no SMTP credentials by default — logging the content (including
    any invite/verification link inside html_body) keeps onboarding fully testable
    without real email infra.
    """
    if not settings.SMTP_HOST:
        logger.info("[email:dev-fallback] to=%s subject=%r\n%s", to_email, subject, html_body)
        return

    message = MIMEText(html_body, "html")
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = to_email

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        server.starttls()
        if settings.SMTP_USER and settings.SMTP_PASSWORD:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.sendmail(settings.SMTP_FROM_EMAIL, [to_email], message.as_string())
