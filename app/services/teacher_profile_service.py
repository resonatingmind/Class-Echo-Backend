from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.class_ import Class
from app.models.chapter import Chapter
from app.models.recording import Recording
from app.models.subject import Subject
from app.models.teacher import Teacher
from app.schemas.teacher import (
    TeacherProfileOut,
    TeacherStatistics,
    TeacherSubjectSummary,
)

async def get_teacher_profile(
    db: AsyncSession,
    teacher_id: int,
) -> TeacherProfileOut:
    
    # -------------------------------------------------------------------------
    # 1. Fetch Teacher with Subject and Class relationships eagerly loaded
    # -------------------------------------------------------------------------
    teacher_result = await db.execute(
        select(Teacher)
        .options(
            selectinload(Teacher.subjects).selectinload(Subject.class_)
        )
        .where(Teacher.id == teacher_id)
    )
    teacher = teacher_result.scalar_one_or_none()

    if teacher is None:
        raise ValueError("Teacher not found")

    if not teacher.subjects:
        return TeacherProfileOut(
            id=teacher.id,
            name=teacher.name,
            email=teacher.email,
            assigned_at=teacher.created_at,
            classes=[],
            subjects=[],
            statistics=TeacherStatistics(
                total_classes=0,
                total_subjects=0,
                total_chapters=0,
                total_recordings=0,
            ),
        )

    # -------------------------------------------------------------------------
    # 2. Bulk Aggregation Query: Fetch chapter & recording counts for all subjects
    # -------------------------------------------------------------------------
    subject_ids = [s.id for s in teacher.subjects]
    
    counts_query = (
        select(
            Subject.id.label("subject_id"),
            func.count(func.distinct(Chapter.id)).label("chapter_count"),
            func.count(func.distinct(Recording.id)).label("recording_count")
        )
        .select_from(Subject)
        .join(Chapter, Chapter.subject_id == Subject.id, isouter=True)
        .join(
            Recording, 
            (Recording.chapter_id == Chapter.id) & (Recording.teacher_id == teacher_id), 
            isouter=True
        )
        .where(Subject.id.in_(subject_ids))
        .group_by(Subject.id)
    )
    
    counts_result = await db.execute(counts_query)
    counts_map = {row.subject_id: row for row in counts_result.all()}

    # -------------------------------------------------------------------------
    # 3. Process results and build output schema structures
    # -------------------------------------------------------------------------
    subjects = []
    class_names = set()
    total_chapters = 0
    total_recordings = 0

    for subject in teacher.subjects:
        # Fallback values if a subject has absolutely no rows linked in the joins
        subject_counts = counts_map.get(subject.id)
        chapter_count = subject_counts.chapter_count if subject_counts else 0
        recording_count = subject_counts.recording_count if subject_counts else 0

        # Resolve class safety fallback strings
        c_name = subject.class_.name if subject.class_ else "Unassigned Class"
        class_names.add(c_name)

        subjects.append(
            TeacherSubjectSummary(
                id=subject.id,
                name=subject.name,
                class_name=c_name,
                chapter_count=chapter_count,
                recording_count=recording_count,
            )
        )
        
        total_chapters += chapter_count
        total_recordings += recording_count

    return TeacherProfileOut(
        id=teacher.id,
        name=teacher.name,
        email=teacher.email,
        assigned_at=teacher.created_at,
        classes=sorted(list(class_names)),
        subjects=subjects,
        statistics=TeacherStatistics(
            total_classes=len(class_names),
            total_subjects=len(subjects),
            total_chapters=total_chapters,
            total_recordings=total_recordings,
        ),
    )