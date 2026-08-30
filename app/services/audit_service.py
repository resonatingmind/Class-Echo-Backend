"""Recording super admin actions as they happen.

Unlike email_log_service and settings_service, this writes through the caller's
own AsyncSession and does *not* commit. That is deliberate: the audit row then
lands in the same transaction as the action it describes, so the two cannot
disagree. An action that rolls back takes its audit row with it, and there is no
window in which the row exists for something that never happened.

The cost is that every caller must reach a commit - which they all already do,
because they are all mutating endpoints.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)

MAX_DETAIL_CHARS = 2000


def record(
    db: AsyncSession,
    *,
    actor,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    target_label: str | None = None,
    school_id: int | None = None,
    detail: str | None = None,
) -> None:
    """Stage an audit row on the caller's session.

    Synchronous because session.add is: the write happens on the caller's
    commit, not here. `actor` is the Admin object the endpoint already holds.
    """
    try:
        db.add(
            AuditLog(
                actor_id=getattr(actor, "id", None),
                # Falls back rather than raising: an audit trail that refuses to
                # record because it could not name the actor is worse than one
                # that records "unknown".
                actor_email=getattr(actor, "email", None) or "unknown",
                action=action,
                target_type=target_type,
                target_id=target_id,
                target_label=target_label[:320] if target_label else None,
                school_id=school_id,
                detail=detail[:MAX_DETAIL_CHARS] if detail else None,
            )
        )
    except Exception:
        # Staging a row should not be able to fail, but an audit helper that can
        # take down the action it is describing would defeat its own purpose.
        logger.warning("Could not stage an audit row for %s", action, exc_info=True)
