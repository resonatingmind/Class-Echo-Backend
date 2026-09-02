from sqlalchemy import case, distinct, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.class_ import Class
from app.models.subject import Subject
from app.models.teacher import Teacher
from app.schemas.class_filters import ClassFiltersOut, TeacherFilterOption


# Canonical academic ordering. Plain alphabetical sorting would put
# "Class 10" between "Class 1" and "Class 2", so class names are mapped to
# their position here; anything unrecognised sorts after the known names.
CLASS_NAME_ORDER = [
    "Nursery",
    "LKG",
    "UKG",
    "Class 1",
    "Class 2",
    "Class 3",
    "Class 4",
    "Class 5",
    "Class 6",
    "Class 7",
    "Class 8",
    "Class 9",
    "Class 10",
    "Class 11",
    "Class 12",
]


def class_name_rank() -> ColumnElement[int]:
    return case(
        {name: index for index, name in enumerate(CLASS_NAME_ORDER)},
        value=Class.name,
        else_=len(CLASS_NAME_ORDER),
    )


def sort_class_names(names: list[str]) -> list[str]:
    ranks = {name: index for index, name in enumerate(CLASS_NAME_ORDER)}
    return sorted(names, key=lambda name: (ranks.get(name, len(ranks)), name))


async def get_class_filters(
    db: AsyncSession,
    school_id: int,
) -> ClassFiltersOut:

    class_rows = await db.execute(
        select(distinct(Class.name)).where(Class.school_id == school_id)
    )

    section_rows = await db.execute(
        select(distinct(Class.section))
        .where(Class.school_id == school_id)
        .order_by(Class.section)
    )

    subject_rows = await db.execute(
        select(distinct(Subject.name))
        .join(Class, Subject.class_id == Class.id)
        .where(Class.school_id == school_id)
        .order_by(Subject.name)
    )

    teacher_rows = await db.execute(
        select(Teacher.id, Teacher.name)
        .join(Subject, Subject.teacher_id == Teacher.id)
        .join(Class, Subject.class_id == Class.id)
        .where(Class.school_id == school_id)
        .group_by(Teacher.id, Teacher.name)
        .order_by(Teacher.name)
    )

    return ClassFiltersOut(
        classes=sort_class_names(list(class_rows.scalars().all())),
        sections=list(section_rows.scalars().all()),
        teachers=[
            TeacherFilterOption(id=row.id, name=row.name)
            for row in teacher_rows.all()
        ],
        subjects=list(subject_rows.scalars().all()),
    )
