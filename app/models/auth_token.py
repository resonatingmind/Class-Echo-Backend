from datetime import datetime
from sqlalchemy import String, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class AuthToken(Base):
    """Shared token store for invite and email-verification links.

    Polymorphic on (user_type, user_id) rather than a hard FK, consistent with
    how login already resolves accounts across separate per-role tables.
    """

    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_type: Mapped[str] = mapped_column(String(30))  # "school_admin" | "teacher"
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    purpose: Mapped[str] = mapped_column(String(30))  # "invite" | "email_verification"
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
