from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

BRIEF_OPEN_SOURCES = ("dashboard", "push")


class ClassBriefOpen(Base):
    """The teacher saw the next-class brief for one timetabled period on one day.

    The brief's success metric: opened before at least 40% of timetabled
    classes. Only the first open of the day is kept, with where it came from.
    """

    __tablename__ = "class_brief_opens"
    __table_args__ = (
        UniqueConstraint("teacher_id", "timetable_entry_id", "on_date", name="uq_class_brief_open"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)
    timetable_entry_id: Mapped[int] = mapped_column(
        ForeignKey("timetable_entries.id", ondelete="CASCADE"), index=True,
    )
    # The teacher's local date of the class.
    on_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(20))
    opened_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
