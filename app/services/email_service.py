import logging
import smtplib
from email.mime.text import MIMEText

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MAILJET_SEND_URL = "https://api.mailjet.com/v3.1/send"
# Mailjet occasionally takes a few seconds under load; the caller is a Celery
# task with retries, so failing fast is better than holding a worker slot.
MAILJET_TIMEOUT_SECONDS = 15.0


class EmailDeliveryError(RuntimeError):
    """Raised when the provider rejected the message.

    Celery's send_email_task retries on this, so a transient Mailjet outage does
    not silently swallow an invite.
    """


def send_email(to_email: str, subject: str, html_body: str, to_name: str | None = None) -> None:
    """Send an email through whichever backend is configured.

    Mailjet wins when its API keys are present, SMTP is the fallback, and with
    neither configured the message is logged instead. The log fallback keeps
    onboarding fully testable locally: the invite link is right there in the
    server output.
    """
    backend = settings.email_backend

    if backend == "mailjet":
        _send_via_mailjet(to_email, subject, html_body, to_name)
        return

    if backend == "smtp":
        _send_via_smtp(to_email, subject, html_body)
        return

    logger.info("[email:dev-fallback] to=%s subject=%r\n%s", to_email, subject, html_body)


def _send_via_mailjet(to_email: str, subject: str, html_body: str, to_name: str | None) -> None:
    payload = {
        "Messages": [
            {
                "From": {
                    "Email": settings.MAILJET_FROM_EMAIL,
                    "Name": settings.MAILJET_FROM_NAME,
                },
                "To": [{"Email": to_email, "Name": to_name or to_email}],
                "Subject": subject,
                "HTMLPart": html_body,
                # Mailjet requires a text alternative for good deliverability;
                # without one, several providers score the mail as spam.
                "TextPart": _html_to_text(html_body),
            }
        ]
    }
    if settings.MAILJET_SANDBOX:
        # Validates the whole request against Mailjet without delivering, which
        # is how staging verifies credentials without mailing real teachers.
        payload["SandboxMode"] = True

    try:
        response = httpx.post(
            MAILJET_SEND_URL,
            json=payload,
            auth=(settings.MAILJET_API_KEY or "", settings.MAILJET_API_SECRET or ""),
            timeout=MAILJET_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise EmailDeliveryError(f"Could not reach Mailjet: {exc}") from exc

    if response.status_code == 401:
        raise EmailDeliveryError(
            "Mailjet rejected the API key/secret. Check MAILJET_API_KEY and "
            "MAILJET_API_SECRET."
        )
    if response.status_code >= 400:
        raise EmailDeliveryError(
            f"Mailjet returned {response.status_code}: {response.text[:500]}"
        )

    # A 200 does not mean delivered: Mailjet reports per-recipient failures
    # inside the body, most commonly an unverified sender address.
    body = response.json()
    for message in body.get("Messages", []):
        if message.get("Status") != "success":
            errors = message.get("Errors") or []
            detail = "; ".join(
                f"{e.get('ErrorCode')}: {e.get('ErrorMessage')}" for e in errors
            ) or str(message)
            raise EmailDeliveryError(f"Mailjet could not send to {to_email} - {detail}")

    logger.info("[email:mailjet] sent to=%s subject=%r", to_email, subject)


def _send_via_smtp(to_email: str, subject: str, html_body: str) -> None:
    message = MIMEText(html_body, "html")
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = to_email

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM_EMAIL, [to_email], message.as_string())
    except smtplib.SMTPException as exc:
        raise EmailDeliveryError(f"SMTP delivery failed: {exc}") from exc

    logger.info("[email:smtp] sent to=%s subject=%r", to_email, subject)


def _html_to_text(html_body: str) -> str:
    """Crude tag strip for the plain-text alternative.

    Deliberately not a full HTML parser: our own templates are the only input,
    and they are simple enough that dropping tags reads fine.
    """
    import re

    text = re.sub(r"<br\s*/?>", "\n", html_body, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|h1|h2|h3|tr)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
