from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.teacher import Teacher
from app.models.recording import Recording
from app.schemas.teacher_recordings import TeacherRecordingOut


async def get_teacher_recordings(
    db: AsyncSession,
    teacher_id: int,
) -> list[TeacherRecordingOut]:

    teacher_result = await db.execute(
        select(Teacher).where(Teacher.id == teacher_id)
    )

    teacher = teacher_result.scalar_one_or_none()

    if teacher is None:
        raise ValueError("Teacher not found")

    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.subject),
            selectinload(Recording.chapter),
            selectinload(Recording.report),
        )
        .where(Recording.teacher_id == teacher_id)
        .order_by(Recording.uploaded_at.desc())
    )

    recordings = result.scalars().all()

    return [
        TeacherRecordingOut(
            id=recording.id,
            lecture_title=recording.lecture_title,
            chapter_name=(
                recording.chapter.name
                if recording.chapter
                else None
            ),
            uploaded_at=recording.uploaded_at,
            status=recording.status.value,
            overall_score=(
                recording.report.overall_score
                if recording.report
                else None
            ),
            subject_name=recording.subject.name,
            cloudinary_url=recording.cloudinary_url,
            duration_seconds=recording.duration_seconds,
        )
        for recording in recordings
    ]
    
async def get_chapter_recordings(
    db: AsyncSession,
    chapter_id: int,
) -> list[TeacherRecordingOut]:

    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.subject),
            selectinload(Recording.chapter),
            selectinload(Recording.report),
        )
        .where(Recording.chapter_id == chapter_id)
        .order_by(Recording.uploaded_at.desc())
    )

    recordings = result.scalars().all()

    return [
        TeacherRecordingOut(
            id=recording.id,
            lecture_title=recording.lecture_title,
            chapter_name=(
                recording.chapter.name
                if recording.chapter
                else None
            ),
            uploaded_at=recording.uploaded_at,
            status=recording.status.value,
            overall_score=(
                recording.report.overall_score
                if recording.report
                else None
            ),
            subject_name=recording.subject.name,
            cloudinary_url=recording.cloudinary_url,
            duration_seconds=recording.duration_seconds,
        )
        for recording in recordings
    ]