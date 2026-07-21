from datetime import datetime, time
from sqlalchemy import String, DateTime, ForeignKey, Integer, Time, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class TimetableEntry(Base):
    """A single scheduled period on the school timetable.

    day_of_week is stored as the day name ("Monday" ... "Saturday") to match
    the table already provisioned in the database. Every period requires a
    subject and a teacher; a teacher cannot be double-booked into two
    different classes for the same day/period/academic year.
    """

    __tablename__ = "timetable_entries"

    __table_args__ = (
        UniqueConstraint(
            "class_id", "day_of_week", "period_number", "academic_year",
            name="uq_class_period",
        ),
        UniqueConstraint(
            "teacher_id", "day_of_week", "period_number", "academic_year",
            name="uq_teacher_period",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), index=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"), index=True)

    day_of_week: Mapped[str] = mapped_column(String(20))
    period_number: Mapped[int] = mapped_column(Integer)

    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)

    room: Mapped[str | None] = mapped_column(String(100), nullable=True)
    academic_year: Mapped[str] = mapped_column(String(20), server_default="2026-27")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    school: Mapped["School"] = relationship("School")
    class_: Mapped["Class"] = relationship("Class")
    subject: Mapped["Subject"] = relationship("Subject")
    teacher: Mapped["Teacher"] = relationship("Teacher")
