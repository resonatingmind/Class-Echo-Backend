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
):
    from app.services.email_service import send_email

    try:
        send_email(to_email, subject, html_body, to_name)
    except Exception as exc:
        logger.exception("Failed to send email to %s", to_email)
        # Backs off across retries: a Mailjet blip clears in seconds, but a rate
        # limit needs longer than a fixed 30s gap three times in a row.
        countdown = 30 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)
