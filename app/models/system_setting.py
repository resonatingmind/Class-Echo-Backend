from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SystemSetting(Base):
    """An operational value overridden at runtime instead of at deploy time.

    Only keys declared in settings_service.SPECS are accepted, and none of them
    is a credential. Secrets stay in the environment: a key in this table would
    end up in database backups, in any admin's browser, and in whatever logs a
    query lands in - and a bad value written here could lock out the very screen
    you would use to correct it, with no redeploy in the loop to save you.

    A row existing means "overridden". Deleting it falls back to the environment
    variable, which is why reset is a DELETE rather than a write of the default.
    """

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    # Free text rather than a foreign key: this is an audit breadcrumb, and it
    # should still read sensibly after the admin account is gone.
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
