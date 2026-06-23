from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    subject: Mapped["Subject"] = relationship("Subject", back_populates="chapters")
    documents: Mapped[list["ChapterDocument"]] = relationship("ChapterDocument", back_populates="chapter")
    chunks: Mapped[list["ChapterChunk"]] = relationship("ChapterChunk", back_populates="chapter")
    recordings: Mapped[list["Recording"]] = relationship("Recording", back_populates="chapter")
    knowledge: Mapped["ChapterKnowledge | None"] = relationship(
        "ChapterKnowledge", back_populates="chapter", uselist=False, cascade="all, delete-orphan"
    )
