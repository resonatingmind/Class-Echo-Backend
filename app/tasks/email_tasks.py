import logging
from celery_worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3)
def send_email_task(
    self,
    to_email: str,
    subject: str,
    html_body: str,
    to_name: str | None = None,
    log_id: int | None = None,
):
    from app.config import settings
    from app.models.email_log import EmailStatus
    from app.services import email_log_service
    from app.services.email_service import email_is_paused, send_email

    try:
        send_email(to_email, subject, html_body, to_name)
    except Exception as exc:
        logger.exception("Failed to send email to %s", to_email)
        # Written on every attempt, not just the last: while the retries are
        # still running the admin screen should show the current error rather
        # than a row stuck on "queued" for the next four minutes.
        email_log_service.mark(log_id, EmailStatus.failed, str(exc))
        # Backs off across retries: a Mailjet blip clears in seconds, but a rate
        # limit needs longer than a fixed 30s gap three times in a row.
        countdown = 30 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)

    # A later retry succeeding has to clear the earlier failure, or the log
    # keeps accusing a message that did in fact arrive. "skipped" covers the two
    # ways send_email returns without delivering: no provider configured, or
    # outgoing mail paused from the admin panel.
    email_log_service.mark(
        log_id,
        EmailStatus.sent
        if settings.email_backend != "log" and not email_is_paused()
        else EmailStatus.skipped,
    )
