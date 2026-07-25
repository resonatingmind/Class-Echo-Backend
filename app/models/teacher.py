from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    school_id: Mapped[int | None] = mapped_column(ForeignKey("schools.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Invite / approval lifecycle: invited -> pending_approval -> active, or invited/pending_approval -> rejected,
    # active -> suspended -> active.
    status: Mapped[str] = mapped_column(String(30), server_default="active", nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    invited_by_school_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("school_admins.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by_school_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("school_admins.id"), nullable=True
    )
    rejected_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    school: Mapped["School | None"] = relationship("School")
    subjects: Mapped[list["Subject"]] = relationship("Subject", back_populates="teacher")
    recordings: Mapped[list["Recording"]] = relationship("Recording", back_populates="teacher")
