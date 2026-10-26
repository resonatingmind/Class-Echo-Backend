import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FeedbackTarget(str, enum.Enum):
    strength = "strength"
    dimension = "dimension"
    improvement = "improvement"


class FeedbackVerdict(str, enum.Enum):
    agree = "agree"
    disagree = "disagree"


class ReportFeedback(Base):
    """A teacher's 👍/👎 on one finding of their report.

    A disagreement never changes the score; it is shown next to the finding
    wherever the report is read, admins included. target_key is the dimension
    name for dimension findings and the list index for strengths and
    improvements, so feedback is cleared whenever a report is re-scored.
    """

    __tablename__ = "report_feedback"
    __table_args__ = (
        UniqueConstraint("report_id", "target_type", "target_key", name="uq_report_feedback_target"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("llm_reports.id", ondelete="CASCADE"), index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(20))  # FeedbackTarget
    target_key: Mapped[str] = mapped_column(String(50))
    # The dimension the finding belongs to, when known — what the disagreement
    # summary groups by.
    area: Mapped[str | None] = mapped_column(String(50), nullable=True)
    verdict: Mapped[str] = mapped_column(String(20), index=True)  # FeedbackVerdict
    reasons: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    report: Mapped["LLMReport"] = relationship("LLMReport", back_populates="feedback")
