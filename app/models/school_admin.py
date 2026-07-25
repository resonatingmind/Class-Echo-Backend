from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class SchoolAdmin(Base):
    __tablename__ = "school_admins"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_pic_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Invite lifecycle: invited (no password yet, admin-created) -> active (accepted invite, set own password).
    status: Mapped[str] = mapped_column(String(30), server_default="active", nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    invited_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admins.id"), nullable=True)

    school: Mapped["School"] = relationship("School", back_populates="admin")
