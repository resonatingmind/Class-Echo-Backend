from datetime import datetime
from sqlalchemy import Boolean, String, DateTime, ForeignKey, Float, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class LLMReport(Base):
    __tablename__ = "llm_reports"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"), unique=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    teaching_quality_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    quantitative_metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_llm_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Which prompt/rubric scored this report (see llm_pipeline.config.RUBRIC_VERSION).
    # Scores are only comparable within one version.
    rubric_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # When the teacher first opened the finished report; the open-rate metric.
    first_opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # How many times the teacher played a clip from this report.
    clip_plays: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The teacher left this lecture out of My Growth trends; only allowed once
    # they disputed a finding as not fitting this type of class.
    excluded_from_trends: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    recording: Mapped["Recording"] = relationship("Recording", back_populates="report")
    self_rating: Mapped["SelfRating | None"] = relationship(
        "SelfRating", back_populates="report", uselist=False, cascade="all, delete-orphan", passive_deletes=True,
    )
    feedback: Mapped[list["ReportFeedback"]] = relationship(
        "ReportFeedback", back_populates="report", cascade="all, delete-orphan", passive_deletes=True,
    )
    shared_clips: Mapped[list["SharedClip"]] = relationship(
        "SharedClip", back_populates="report", cascade="all, delete-orphan", passive_deletes=True,
        order_by="SharedClip.start",
    )
