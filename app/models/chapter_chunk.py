from datetime import datetime
from sqlalchemy import Text, DateTime, ForeignKey, JSON, func
from sqlalchemy.types import UserDefinedType
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Vector(UserDefinedType):
    def __init__(self, dim=None):
        self.dim = dim

    def get_col_spec(self, **kw):
        if self.dim is not None:
            return f"VECTOR({self.dim})"
        return "VECTOR"

    def bind_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            if isinstance(value, list):
                return "[" + ",".join(map(str, value)) + "]"
            return value
        return process

    def result_processor(self, dialect, coltype):
        def process(value):
            if value is None:
                return None
            if isinstance(value, str):
                value = value.strip("[]")
                if not value:
                    return []
                return [float(x) for x in value.split(",")]
            return value
        return process


class ChapterChunk(Base):
    __tablename__ = "chapter_chunks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id"))
    document_id: Mapped[int] = mapped_column(ForeignKey(
        "chapter_documents.id",
        ondelete="CASCADE",
    ))
    chunk_text: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    embedding_vector: Mapped[list[float]] = mapped_column(Vector(1536))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    chapter: Mapped["Chapter"] = relationship("Chapter", back_populates="chunks")
    document: Mapped["ChapterDocument"] = relationship("ChapterDocument", back_populates="chunks")
