from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class LearningPlan(Base):
    __tablename__ = "learning_plans"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    topic_name: Mapped[str] = mapped_column(String(255))
    sub_topic: Mapped[str] = mapped_column(String(255))
    classwork_activities: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessment_task: Mapped[str | None] = mapped_column(String(255), nullable=True)
    teaching_aids: Mapped[str | None] = mapped_column(String(255), nullable=True)
    learning_outcomes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    chapter: Mapped["Chapter"] = relationship("Chapter", back_populates="learning_plans")
    recordings: Mapped[list["Recording"]] = relationship("Recording", back_populates="learning_plan")
