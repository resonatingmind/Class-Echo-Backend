from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditLog(Base):
    """Who did what, to whom, and when.

    Written for the super admin actions that cannot be inferred afterwards from
    the data they touched: setting somebody else's password leaves a hash that
    looks like any other, suspending a school leaves a status that looks like
    the school changed it, and a deleted record leaves nothing at all. Once more
    than one person holds super admin credentials, none of those is answerable
    without a record kept at the time.

    Every reference is a plain integer or a label rather than a foreign key. An
    audit row has to outlive the thing it describes - a delete is exactly the
    event you most want to still be able to read about, and a foreign key would
    either block the delete or take the evidence with it.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Denormalised on purpose: the email is the part a human recognises, and it
    # must still read correctly after the admin account is renamed or removed.
    actor_email: Mapped[str] = mapped_column(String(320), index=True)

    # Dotted verb, e.g. "school.suspend" or "teacher.set_password".
    action: Mapped[str] = mapped_column(String(64), index=True)

    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Null for targets that are not rows - a settings key, for instance.
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # What the target was called at the time. A name looked up on read would
    # show today's value, which is wrong for a rename and impossible for a
    # delete.
    target_label: Mapped[str | None] = mapped_column(String(320), nullable=True)

    school_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Free text: what changed, or why. Kept human-readable rather than a diff
    # blob, because this gets read by a person under time pressure.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
