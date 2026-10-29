from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# The four plain-language areas a teacher rates, each 1-5. The report maps the
# seven rubric dimensions onto them to show the AI's view side by side.
SELF_RATING_AREAS = ("clarity", "engagement", "coverage", "recap")


class SelfRating(Base):
    """The teacher's own view of a lecture, given before the report is revealed.

    One per report. skipped=True records a one-tap skip, so the skip rate can
    be measured and the teacher isn't asked again. Private to the teacher.
    """

    __tablename__ = "self_ratings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("llm_reports.id", ondelete="CASCADE"), unique=True, index=True,
    )
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)
    # {area: 1-5} for the areas the teacher rated; empty when skipped.
    ratings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    reflection: Mapped[str | None] = mapped_column(Text, nullable=True)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    report: Mapped["LLMReport"] = relationship("LLMReport", back_populates="self_rating")
