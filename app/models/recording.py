import enum
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey, Float, Enum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class RecordingStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"))
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id"), nullable=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"))
    chapter_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lecture_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcription: Mapped[str | None] = mapped_column(Text, nullable=True)
    cloudinary_url: Mapped[str] = mapped_column(String(500))
    cloudinary_public_id: Mapped[str] = mapped_column(String(255))
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[RecordingStatus] = mapped_column(
        Enum(RecordingStatus), default=RecordingStatus.pending
    )
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_archived: Mapped[bool] = mapped_column(default=False, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    subject: Mapped["Subject"] = relationship("Subject", back_populates="recordings")
    teacher: Mapped["Teacher"] = relationship("Teacher", back_populates="recordings")
    report: Mapped["LLMReport | None"] = relationship("LLMReport", back_populates="recording", uselist=False, cascade="all, delete-orphan")
    chapter: Mapped["Chapter | None"] = relationship("Chapter", back_populates="recordings")
