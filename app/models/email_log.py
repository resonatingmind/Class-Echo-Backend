import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EmailStatus(str, enum.Enum):
    queued = "queued"
    sent = "sent"
    failed = "failed"
    # No provider configured: send_email wrote the message to the server log and
    # nothing left the building. Distinct from "sent" because from every other
    # angle - the API response, the account row, the UI - the two look identical.
    skipped = "skipped"


class EmailLog(Base):
    """One row per outgoing message, so "they never got the invite" is answerable.

    Deliberately stores no message body. Invite HTML contains a live single-use
    token, so keeping it here would turn this table - and the admin screen that
    renders it - into a way to take over any account that was ever invited.
    Resending therefore re-mints a token through the normal resend endpoints
    rather than replaying a stored link, which is also the only correct
    behaviour: issue_invite_token burns the previous token, so a replayed link
    would be dead anyway.
    """

    __tablename__ = "email_logs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    to_email: Mapped[str] = mapped_column(String(320), index=True)
    subject: Mapped[str] = mapped_column(String(500))
    # invite | verification | test | general
    purpose: Mapped[str] = mapped_column(String(32), index=True, default="general")
    status: Mapped[EmailStatus] = mapped_column(
        Enum(EmailStatus, name="emailstatus"), default=EmailStatus.queued, index=True
    )
    # Which transport handled it, so a provider switch is visible in the history
    # instead of having to be remembered.
    backend: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Plain integers rather than foreign keys: this is a record of what happened
    # and it has to survive the school or teacher it describes being deleted -
    # which is precisely when you most want to read it back.
    school_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    related_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    related_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
