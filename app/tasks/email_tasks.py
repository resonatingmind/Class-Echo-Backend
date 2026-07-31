import logging
from celery_worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3)
def send_email_task(self, to_email: str, subject: str, html_body: str):
    from app.services.email_service import send_email

    try:
        send_email(to_email, subject, html_body)
    except Exception as exc:
        logger.exception("Failed to send email to %s", to_email)
        raise self.retry(exc=exc, countdown=30)
