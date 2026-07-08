from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.subject import Subject
from app.models.class_ import Class
from app.models.teacher import Teacher
from app.schemas.teacher_analytics_filters import (
    AnalyticsFiltersOut,
    FilterOption,
)


async def get_teacher_analytics_filters(
    db: AsyncSession,
    teacher_id: int,
) -> AnalyticsFiltersOut:

    teacher = await db.get(Teacher, teacher_id)

    if teacher is None:
        raise ValueError("Teacher not found")

    subject_result = await db.execute(
        select(Subject)
        .options(
            selectinload(Subject.class_)
        )
        .join(Class)
        .where(
            Subject.teacher_id == teacher_id,
            Class.school_id == teacher.school_id,
        )
        .order_by(Class.name, Subject.name)
    )

    subjects = subject_result.scalars().all()

    classes = {}

    subject_items = []

    for subject in subjects:

        subject_items.append(
            FilterOption(
                id=subject.id,
                name=subject.name,
            )
        )

        if subject.class_.id not in classes:

            classes[subject.class_.id] = FilterOption(
                id=subject.class_.id,
                name=subject.class_.name,
            )

    return AnalyticsFiltersOut(
        classes=list(classes.values()),

        subjects=subject_items,

        statuses=[
            "completed",
            "pending",
            "failed",
        ],

        semesters=[],
    )