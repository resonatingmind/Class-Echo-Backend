from datetime import datetime
from sqlalchemy import Text, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ChapterKnowledge(Base):
    __tablename__ = "chapter_knowledge"

    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    chapter_summary: Mapped[str] = mapped_column(Text, nullable=False)
    learning_objectives: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    key_concepts: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    must_cover_topics: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    common_misconceptions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    chapter: Mapped["Chapter"] = relationship("Chapter", back_populates="knowledge")
