from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subject import Subject
from app.models.class_ import Class

from app.schemas.planner import (
    PlannerItemOut,
    PlannerChapterOut,
    PlannerStatisticsOut,
    PlannerSubjectDetailOut,
)

from app.models.chapter import Chapter
from app.models.recording import Recording

from fastapi import HTTPException
from app.models.recording import RecordingStatus

async def get_school_planner(
    db: AsyncSession,
    school_id: int,
) -> list[PlannerItemOut]:

    result = await db.execute(
        select(Subject)
        .options(
            selectinload(Subject.teacher),
            selectinload(Subject.class_),
            selectinload(Subject.chapters),
            selectinload(Subject.recordings),
        )
        .join(Class, Subject.class_id == Class.id)
        .where(Class.school_id == school_id)
        .order_by(
            Class.name,
            Subject.name,
        )
    )

    subjects = result.scalars().all()

    planner_items: list[PlannerItemOut] = []

    for subject in subjects:

        planner_items.append(
            PlannerItemOut(
                subject_id=subject.id,

                class_id=subject.class_.id,
                class_name=subject.class_.name,
                section=subject.class_.section,
                teacher_id=subject.teacher.id if subject.teacher else None,
                teacher_name=subject.teacher.name if subject.teacher else None,

                subject_name=subject.name,

                academic_year="2026-2027",

                chapter_count=len(subject.chapters),

                recording_count=len(subject.recordings),

                created_at=subject.created_at,
                
                subject_profile_image_url=subject.profile_image_url,
                teacher_profile_image_url=(
                        subject.teacher.profile_image_url
                        if subject.teacher
                        else None
                ),
            )
        )
    
    return planner_items


async def get_planner_subject_detail(
    db: AsyncSession,
    school_id: int,
    subject_id: int,
) -> PlannerSubjectDetailOut:

    result = await db.execute(
        select(Subject)
        .options(
            selectinload(Subject.class_),
            selectinload(Subject.teacher),
            selectinload(Subject.chapters)
            .selectinload(Chapter.documents),
            selectinload(Subject.chapters)
            .selectinload(Chapter.recordings)
            .selectinload(Recording.report),
        )
        .join(Class)
        .where(
            Subject.id == subject_id,
            Class.school_id == school_id,
        )
    )

    subject = result.scalar_one_or_none()

    if subject is None:
        raise HTTPException(
            status_code=404,
            detail="Subject not found.",
        )

    chapters = []

    total_documents = 0
    total_recordings = 0

    overall_scores: list[float] = []

    for chapter in subject.chapters:

        document_count = len(chapter.documents)

        recording_count = len(chapter.recordings)

        total_documents += document_count
        total_recordings += recording_count

        scores: list[float] = []

        processing_found = False

        for recording in chapter.recordings:

            if recording.status in (
                RecordingStatus.pending,
                RecordingStatus.processing,
            ):
                processing_found = True

            if (
                recording.report is not None
                and recording.report.overall_score is not None
            ):
                score = float(recording.report.overall_score)

                scores.append(score)

                overall_scores.append(score)

        average_score = (
            round(sum(scores) / len(scores), 1)
            if scores
            else None
        )

        if recording_count == 0:
            status = "Pending"

        elif processing_found:
            status = "Processing"

        else:
            status = "Completed"

        chapters.append(
            PlannerChapterOut(
                id=chapter.id,
                
                name=chapter.name,

                subject_id=subject.id,

                teacher_id=subject.teacher.id if subject.teacher else None,

                document_count=document_count,

                recording_count=recording_count,

                average_score=average_score,

                status=status,
            )
        )

    overall_average = (
        round(sum(overall_scores) / len(overall_scores), 1)
        if overall_scores
        else None
    )

    return PlannerSubjectDetailOut(
        subject_id=subject.id,

        subject_name=subject.name,

        class_name=subject.class_.name,

        teacher_name=(
            subject.teacher.name
            if subject.teacher
            else None
        ),

        academic_year="2026-2027",

        statistics=PlannerStatisticsOut(
            total_chapters=len(subject.chapters),

            total_documents=total_documents,

            total_recordings=total_recordings,

            average_score=overall_average,
        ),

        chapters=chapters,
    )