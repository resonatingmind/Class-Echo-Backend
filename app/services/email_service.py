import logging
import smtplib
import time
from email.mime.text import MIMEText

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MAILJET_SEND_URL = "https://api.mailjet.com/v3.1/send"
# Mailjet occasionally takes a few seconds under load; the caller is a Celery
# task with retries, so failing fast is better than holding a worker slot.
MAILJET_TIMEOUT_SECONDS = 15.0
# smtplib defaults to the global socket timeout, which is None: without this a
# stalled connection to the mail host hangs the Celery worker forever. It never
# raises, so send_email_task's retries never fire either and the invite simply
# stops existing — the same invisible failure the worker probe above guards
# against, one layer down. 30s covers Gmail's slowest observed handshake.
SMTP_TIMEOUT_SECONDS = 30.0


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


def credential_fingerprint() -> str | None:
    """Identify the Mailjet credentials in use without revealing them.

    "I already set the key" and "the key Mailjet is rejecting" are different
    claims, and nothing in a 401 tells them apart. Length plus the first and
    last four characters is enough to spot the three ways this actually goes
    wrong — a truncated paste, the key and secret swapped, or a stale value on
    the deployment — while staying useless to anyone who reads it.
    """
    if not settings.mailjet_configured:
        return None

    def redact(label: str, value: str | None) -> str:
        if not value:
            return f"{label}: missing"
        if len(value) < 12:
            # Too short to show any of: whatever this is, it is not a real key.
            return f"{label}: len={len(value)} (too short to be valid)"
        return f"{label}: len={len(value)} {value[:4]}…{value[-4:]}"

    return "  ".join(
        (
            redact("key", settings.MAILJET_API_KEY),
            redact("secret", settings.MAILJET_API_SECRET),
        )
    )


# ── Choosing between the worker and an inline send ───────────────────────────

_WORKER_PROBE_TTL_SECONDS = 60
# (checked_at_monotonic, worker_was_listening)
_worker_probe: tuple[float, bool] | None = None


def _celery_worker_listening() -> bool:
    """Is a worker actually consuming, or is the broker merely reachable?

    `.delay()` returns happily as long as Redis accepts the message, so a
    deployment running the API without its Celery worker queues every invite
    into a list nobody reads and reports success for all of them. That is the
    one email failure with no error anywhere — in the API log, the worker log or
    the provider dashboard. One ping a minute buys the difference.
    """
    global _worker_probe

    now = time.monotonic()
    if _worker_probe is not None and now - _worker_probe[0] < _WORKER_PROBE_TTL_SECONDS:
        return _worker_probe[1]

    try:
        from celery_worker import celery_app

        listening = bool(celery_app.control.ping(timeout=1.0))
    except Exception:
        logger.warning(
            "Could not probe for a Celery worker; sending email inline instead",
            exc_info=True,
        )
        listening = False

    if not listening:
        logger.warning(
            "No Celery worker answered: email will be sent inline on the web "
            "process. Start the worker (celery -A celery_worker.celery_app "
            "worker) so delivery does not block API requests."
        )

    _worker_probe = (now, listening)
    return listening


def deliver_email(
    to_email: str, subject: str, html_body: str, to_name: str | None = None
) -> str:
    """Get an email out by whichever route is actually working.

    Prefers the Celery worker so a slow provider cannot stall an API request,
    but falls back to sending inline rather than queuing into the void. Returns
    "queued", "sent" or "failed" — never raises, because every caller has
    already committed the account row the email is about, and a 500 at that
    point would report failure for work that succeeded.
    """
    if _celery_worker_listening():
        try:
            from app.tasks.email_tasks import send_email_task

            send_email_task.delay(to_email, subject, html_body, to_name)
            return "queued"
        except Exception:
            logger.warning(
                "Handing the email for %s to Celery failed; sending inline",
                to_email,
                exc_info=True,
            )

    try:
        send_email(to_email, subject, html_body, to_name)
        return "sent"
    except Exception:
        logger.exception(
            "Email to %s could not be delivered. Check /admin/system/email-test "
            "for the provider's own error, then resend.",
            to_email,
        )
        return "failed"


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
        # A 401 here is not only "wrong key pair". Mailjet also returns it for a
        # blocked or not-yet-validated account (ErrorCode mj-0001), which needs a
        # support ticket rather than a new key — and the read endpoints keep
        # answering 200 the whole time, so the credentials look fine everywhere
        # you would think to check. Its own body is the only thing that tells the
        # two apart, so carry it through instead of asserting a credential fault.
        raise EmailDeliveryError(
            f"Mailjet rejected the send with 401: {response.text[:500]}"
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
        with smtplib.SMTP(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS
        ) as server:
            server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM_EMAIL, [to_email], message.as_string())
    # OSError covers the timeout above and a refused/dropped connection, neither
    # of which is an SMTPException — without it those escape as bare socket
    # errors and lose the "which email, which transport" framing.
    except (smtplib.SMTPException, OSError) as exc:
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
