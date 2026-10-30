from datetime import datetime
from sqlalchemy import String, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class School(Base):
    __tablename__ = "schools"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # A week counts towards a teacher's streak with at least this many recordings.
    streak_min_recordings: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    admin: Mapped["SchoolAdmin"] = relationship("SchoolAdmin", back_populates="school", uselist=False)
    classes: Mapped[list["Class"]] = relationship("Class", back_populates="school")
    students: Mapped[list["Student"]] = relationship("Student", back_populates="school")
