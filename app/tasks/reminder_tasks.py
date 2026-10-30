import logging

from celery_worker import celery_app

logger = logging.getLogger(__name__)

# A day is long enough: the key has the date in it.
CLAIM_TTL_SECONDS = 24 * 60 * 60


@celery_app.task(name="send_class_reminders", ignore_result=True)
def send_class_reminders():
    """Run by Celery beat every 5 minutes; see class_reminder_service."""
    import redis
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.config import settings
    from app.services.class_reminder_service import send_due_reminders

    r = redis.Redis.from_url(settings.REDIS_URL)
    engine = create_engine(settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        with sessionmaker(bind=engine)() as session:
            send_due_reminders(session, claim=lambda key: bool(r.set(key, 1, nx=True, ex=CLAIM_TTL_SECONDS)))
    finally:
        engine.dispose()
