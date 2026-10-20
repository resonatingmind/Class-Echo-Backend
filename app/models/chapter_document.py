from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ChapterDocument(Base):
    __tablename__ = "chapter_documents"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id"))
    file_name: Mapped[str] = mapped_column(String(255))
    storage_url: Mapped[str] = mapped_column(String(500))
    file_type: Mapped[str] = mapped_column(String(50))
    processing_status: Mapped[str] = mapped_column(String(50), default="pending")
    # Why processing FAILED, in words the uploader can act on. Cleared on retry.
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    chapter: Mapped["Chapter"] = relationship("Chapter", back_populates="documents")
    chunks: Mapped[list["ChapterChunk"]] = relationship("ChapterChunk", back_populates="document",
    cascade="all, delete-orphan",
    passive_deletes=True,)

