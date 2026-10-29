from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SharedClip(Base):
    """A moment of a lecture the teacher chose to show their school admin.

    Clips are private to the teacher by default; a row here is the teacher's
    explicit choice to share one. The admin plays the same window of the
    recording as the teacher does (a little before and after start).
    """

    __tablename__ = "shared_clips"
    __table_args__ = (UniqueConstraint("report_id", "start", name="uq_shared_clips_report_start"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("llm_reports.id", ondelete="CASCADE"), index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)
    # Second of the recording the clip is centred on.
    start: Mapped[float] = mapped_column(Float)
    title: Mapped[str] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    report: Mapped["LLMReport"] = relationship("LLMReport", back_populates="shared_clips")
