from datetime import datetime
from sqlalchemy import String, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Class(Base):
    __tablename__ = "classes"
    
    __table_args__ = (
        UniqueConstraint(
            "school_id",
            "name",
            "section",
            name="uq_school_class_section",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255))

    section: Mapped[str] = mapped_column(
        String(50),
        default="A",
        server_default="A",
    )
    
    profile_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    school: Mapped["School"] = relationship("School", back_populates="classes")
    subjects: Mapped[list["Subject"]] = relationship("Subject", back_populates="class_")
    students: Mapped[list["Student"]] = relationship("Student", back_populates="class_")
