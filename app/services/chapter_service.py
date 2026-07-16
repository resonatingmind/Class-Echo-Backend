from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.chapter import Chapter
from app.models.subject import Subject
from app.models.class_ import Class
from app.schemas.chapter import ChapterCreate, ChapterUpdate

from app.models.subject import Subject
from app.models.teacher import Teacher


async def get_owned_subject(db: AsyncSession, subject_id: int, school_id: int) -> Subject:
    result = await db.execute(
        select(Subject)
        .join(Class, Subject.class_id == Class.id)
        .where(Subject.id == subject_id, Class.school_id == school_id)
    )
    subject = result.scalar_one_or_none()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


async def get_owned_chapter(db: AsyncSession, chapter_id: int, school_id: int) -> Chapter:
    result = await db.execute(
        select(Chapter)
        .options(selectinload(Chapter.documents))
        .join(Subject, Chapter.subject_id == Subject.id)
        .join(Class, Subject.class_id == Class.id)
        .where(Chapter.id == chapter_id, Class.school_id == school_id)
    )
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return chapter


async def create_chapter(
    db: AsyncSession, subject_id: int, school_id: int, schema: ChapterCreate
) -> Chapter:
    await get_owned_subject(db, subject_id, school_id)
    
    chapter = Chapter(
        subject_id=subject_id,
        name=schema.name,
        description=schema.description,
    )
    db.add(chapter)
    await db.commit()
    await db.refresh(chapter)
    return chapter


async def list_chapters(
    db: AsyncSession, subject_id: int, school_id: int
) -> list[Chapter]:
    await get_owned_subject(db, subject_id, school_id)
    
    result = await db.execute(
        select(Chapter)
        .options(selectinload(Chapter.documents))
        .where(Chapter.subject_id == subject_id)
        .order_by(Chapter.created_at.asc())
    )
    return list(result.scalars().all())


async def get_chapter_details(
    db: AsyncSession, chapter_id: int, school_id: int
) -> Chapter:
    return await get_owned_chapter(db, chapter_id, school_id)


async def update_chapter(
    db: AsyncSession, chapter_id: int, school_id: int, schema: ChapterUpdate
) -> Chapter:
    chapter = await get_owned_chapter(db, chapter_id, school_id)
    payload = schema.model_dump(exclude_unset=True)
    for field, value in payload.items():
        setattr(chapter, field, value)
    await db.commit()
    await db.refresh(chapter)
    return chapter


async def delete_chapter(
    db: AsyncSession, chapter_id: int, school_id: int
) -> None:
    chapter = await get_owned_chapter(db, chapter_id, school_id)
    await db.delete(chapter)
    await db.commit()


async def get_teacher_owned_subject(
    db: AsyncSession,
    subject_id: int,
    teacher_id: int,
) -> Subject:
    result = await db.execute(
        select(Subject)
        .options(
            selectinload(Subject.teacher),
            selectinload(Subject.class_),
        )
        .where(
            Subject.id == subject_id,
            Subject.teacher_id == teacher_id,
        )
    )

    subject = result.scalar_one_or_none()

    if subject is None:
        raise HTTPException(
            status_code=404,
            detail="Subject not found or not assigned to you",
        )

    return subject


async def get_teacher_owned_chapter(
    db: AsyncSession,
    chapter_id: int,
    teacher_id: int,
) -> Chapter:
    result = await db.execute(
        select(Chapter)
        .options(
            selectinload(Chapter.documents),
            selectinload(Chapter.subject),
        )
        .join(
            Subject,
            Chapter.subject_id == Subject.id,
        )
        .where(
            Chapter.id == chapter_id,
            Subject.teacher_id == teacher_id,
        )
    )

    chapter = result.scalar_one_or_none()

    if chapter is None:
        raise HTTPException(
            status_code=404,
            detail="Chapter not found or not assigned to you",
        )

    return chapter