from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ClassBriefMute(Base):
    """A teacher has turned off the next-class brief for one class's subject.

    A row means muted; unmuting deletes it. Keyed by subject, which is already
    one class + one subject.
    """

    __tablename__ = "class_brief_mutes"
    __table_args__ = (UniqueConstraint("teacher_id", "subject_id", name="uq_class_brief_mute"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
