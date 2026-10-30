from celery import Celery
from app.config import settings

celery_app = Celery(
    "scholarmetric",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.llm_tasks",
        "app.tasks.document_tasks",
        "app.tasks.email_tasks",
        "app.tasks.reminder_tasks",
    ],
)

celery_app.conf.update(
    # Recording analysis is minutes of mostly network wait, so a low concurrency
    # made concurrent uploads queue behind each other and look stuck.
    worker_concurrency=6,
    # Long tasks + default prefetching means one busy worker hoards queued jobs
    # while another sits idle.
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Needs a `celery beat` process next to the worker (see the compose files).
    beat_schedule={
        "class-reminders": {"task": "send_class_reminders", "schedule": 5 * 60},
    },
)
