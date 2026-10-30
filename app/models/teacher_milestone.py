from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TeacherMilestone(Base):
    """Something the teacher achieved: an activity, growth or reflection milestone.

    key is the milestone kind, with a suffix where it can be earned more than
    once (focus_achieved:closure_recap, chapter_covered:12). Private to the
    teacher unless they turn shared on.
    """

    __tablename__ = "teacher_milestones"
    __table_args__ = (UniqueConstraint("teacher_id", "key", name="uq_teacher_milestone"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    # When it happened in the teacher's data, not when it was noticed.
    achieved_at: Mapped[datetime] = mapped_column(DateTime)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    shared: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
