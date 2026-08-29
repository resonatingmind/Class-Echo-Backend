"""Recording what happened to every outgoing email.

Writes go through a synchronous session on purpose. The two places that need to
log are in different worlds - deliver_email runs inside async request handlers,
send_email_task runs in a Celery worker with no event loop at all - and a sync
engine is the one thing both can call without either changing shape.
app.tasks.llm_tasks already reaches for the same trick.

Every function here swallows its own errors. A bookkeeping table that can break
an invitation would be worse than no bookkeeping table.
"""

import logging
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.email_log import EmailLog, EmailStatus

logger = logging.getLogger(__name__)

# Provider errors can carry an entire HTML error page; the reason is always in
# the first couple of thousand characters and the rest just bloats the table.
MAX_ERROR_CHARS = 2000

_session_local = None


def _sessions():
    """Lazily built sync sessionmaker, shared for the life of the process."""
    global _session_local
    if _session_local is None:
        url = settings.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
        # pool_pre_ping: a web process can sit idle between invites for longer
        # than the pooler's idle timeout, and a stale connection here would fail
        # the bookkeeping for a mail that actually went out fine.
        _session_local = sessionmaker(bind=create_engine(url, pool_pre_ping=True))
    return _session_local


def record(
    *,
    to_email: str,
    subject: str,
    purpose: str = "general",
    status: EmailStatus = EmailStatus.queued,
    backend: str | None = None,
    school_id: int | None = None,
    related_type: str | None = None,
    related_id: int | None = None,
) -> int | None:
    """Open a log row for a message about to be sent. Returns its id, or None."""
    try:
        with _sessions()() as session:
            row = EmailLog(
                to_email=to_email,
                subject=subject[:500],
                purpose=purpose,
                status=status,
                backend=backend,
                school_id=school_id,
                related_type=related_type,
                related_id=related_id,
            )
            session.add(row)
            session.commit()
            return row.id
    except Exception:
        logger.warning(
            "Could not record the email to %s in email_logs; the send itself is "
            "unaffected",
            to_email,
            exc_info=True,
        )
        return None


def mark(log_id: int | None, status: EmailStatus, error: str | None = None) -> None:
    """Close out a log row with what the provider actually did."""
    if log_id is None:
        return
    try:
        with _sessions()() as session:
            row = session.get(EmailLog, log_id)
            if row is None:
                return
            row.status = status
            row.error = error[:MAX_ERROR_CHARS] if error else None
            row.updated_at = datetime.utcnow()
            session.commit()
    except Exception:
        logger.warning(
            "Could not update email_logs row %s to %s", log_id, status, exc_info=True
        )
