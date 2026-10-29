import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update
from sqlalchemy.orm import selectinload

from app.models.chapter import Chapter

from app.database import get_db
from app.core.deps import get_teacher, get_active_teacher
from app.models.subject import Subject
from app.models.recording import Recording, RecordingStatus
from app.models.llm_report import LLMReport
from app.models.student import Student
from app.schemas.subject import SubjectOut, SubjectWithClassOut
from app.models.report_feedback import FeedbackTarget
from app.models.self_rating import SelfRating
from app.schemas.recording import (
    LLMReportOut,
    RecordingUpdate,
    RecordingWithReport,
    ReportFeedbackIn,
    ReportFeedbackOut,
    SelfRatingIn,
    SelfRatingOut,
)
from app.services import report_feedback_service
from app.schemas.student import StudentOut
from app.schemas.teacher import (
    TeacherOut,
    TeacherUpdate,
    TeacherDashboardOut,
    TeacherDashboardSubjectOut,
)
from app.schemas.chapter import (
    ChapterCreate,
    ChapterUpdate,
    ChapterOut,
)
from app.services.s3_service import upload_audio, upload_image
from app.services import chapter_service
from app.models.chapter_document import ChapterDocument
from app.schemas.chapter import ChapterDocumentOut
from app.schemas.chapter import ChapterKnowledgeOut
from app.services import chapter_knowledge_service
from app.services.s3_service import upload_document
from app.tasks.llm_tasks import process_recording

from app.models.timetable_entry import TimetableEntry
from app.schemas.timetable import TimetableEntryOut
from app.services.timetable_service import build_timetable_entry_out, day_sort_index
from app.schemas.class_brief import ClassBriefMuteIn, ClassBriefOut
from app.services import class_brief_service
from app.schemas.growth import GrowthOut, TrendsExclusionIn, TrendsExclusionOut
from app.services import growth_service

router = APIRouter()

logger = logging.getLogger(__name__)


async def _queue_analysis(db: AsyncSession, recording: Recording) -> None:
    """
    Hand the recording to the Celery worker for analysis.

    The audio itself is already stored and committed by the time we get here, so
    a broker that is down must not fail the request: the unguarded `.delay()`
    returned a 500 for an upload that had actually succeeded, which invited the
    teacher to upload the same lecture again. The recording is instead marked
    failed with a reason, which the detail screen already renders next to a
    "Retry analysis" button.
    """
    try:
        process_recording.delay(recording.id, recording.cloudinary_url)
    except Exception:
        logger.exception(
            "Could not queue analysis for recording %s", recording.id
        )
        recording.status = RecordingStatus.failed
        recording.error_reason = (
            "The audio was saved, but the analysis service could not be "
            "reached. Tap Retry analysis once it is back online."
        )
        await db.commit()


# ─── TIMETABLE (read-only) ─────────────────────────────────────────────────

@router.get("/timetable", response_model=list[TimetableEntryOut])
async def get_my_timetable(
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    """A teacher can view their full weekly timetable but cannot edit it —
    only the school admin manages the schedule."""
    result = await db.execute(
        select(TimetableEntry)
        .options(
            selectinload(TimetableEntry.class_),
            selectinload(TimetableEntry.subject),
            selectinload(TimetableEntry.teacher),
        )
        .where(TimetableEntry.teacher_id == teacher.id)
    )
    entries = sorted(
        result.scalars().all(),
        key=lambda e: (day_sort_index(e.day_of_week), e.period_number),
    )
    return [build_timetable_entry_out(e) for e in entries]


@router.get("/timetable/{entry_id}/brief", response_model=ClassBriefOut)
async def get_class_brief(
    entry_id: int,
    on: date | None = None,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    """The recap shown before a timetabled period. `on` is the teacher's local
    date, which picks a planner entry dated today; the server's date otherwise."""
    entry = await class_brief_service.get_own_entry(db, entry_id, teacher.id)
    return await class_brief_service.build_brief(db, entry, teacher.id, on or date.today())


@router.put("/timetable/{entry_id}/brief/mute", response_model=ClassBriefOut)
async def mute_class_brief(
    entry_id: int,
    body: ClassBriefMuteIn,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    """Turn the brief off (or back on) for this class + subject, every period."""
    entry = await class_brief_service.get_own_entry(db, entry_id, teacher.id)
    await class_brief_service.set_muted(db, teacher.id, entry.subject_id, body.muted)
    return await class_brief_service.build_brief(db, entry, teacher.id, date.today())


def _teacher_out(teacher) -> TeacherOut:
    return TeacherOut(
        id=teacher.id,
        name=teacher.name,
        email=teacher.email,
        profile_image_url=teacher.profile_image_url,
        school_id=teacher.school_id,
        school_name=teacher.school.name if teacher.school else None,
        status=teacher.status,
        email_verified=teacher.email_verified,
        rejected_reason=teacher.rejected_reason,
        created_at=teacher.created_at,
    )


ALLOWED_AUDIO_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/x-m4a",
    "audio/mp4",
    "audio/webm",
    "audio/aac",
}

EXT_TO_MIME = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "aac": "audio/aac",
}


def _resolve_mime(file: UploadFile) -> str:
    """Return MIME type, falling back to extension if content_type is unreliable."""
    ct = (file.content_type or "").split(";")[0].strip().lower()
    if ct in ALLOWED_AUDIO_TYPES:
        return ct
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    return EXT_TO_MIME.get(ext, ct)


@router.get("/me", response_model=TeacherOut)
async def get_my_profile(teacher=Depends(get_teacher)):
    return _teacher_out(teacher)


@router.put("/me", response_model=TeacherOut)
async def update_my_profile(
    body: TeacherUpdate,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(teacher, field, value)
    await db.commit()
    await db.refresh(teacher)
    return _teacher_out(teacher)


@router.post("/profile-image", response_model=TeacherOut)
async def upload_profile_image(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    result = await upload_image(file, folder=f"classecho/teachers/{teacher.id}")
    teacher.profile_image_url = result["url"]
    await db.commit()
    await db.refresh(teacher)
    return _teacher_out(teacher)


@router.get("/subjects/{subject_id}/students", response_model=list[StudentOut])
async def list_subject_students(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    subject = await chapter_service.get_teacher_owned_subject(db,
    subject_id,
    teacher.id,)
    result = await db.execute(
        select(Student)
        .where(Student.class_id == subject.class_id)
        .order_by(Student.name)
    )
    return result.scalars().all()


@router.get("/subjects", response_model=list[SubjectWithClassOut])
async def list_my_subjects(
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    result = await db.execute(
        select(Subject)
        .options(selectinload(Subject.teacher), selectinload(Subject.class_))
        .where(Subject.teacher_id == teacher.id)
        .order_by(Subject.name)
    )
    return result.scalars().all()


@router.get(
    "/dashboard-summary",
    response_model=TeacherDashboardOut,
)
async def teacher_dashboard_summary(
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    subjects_result = await db.execute(
        select(Subject)
        .options(
            selectinload(Subject.class_),
            selectinload(Subject.recordings).selectinload(Recording.report),
        )
        .where(Subject.teacher_id == teacher.id)
        .order_by(Subject.name)
    )

    subjects = subjects_result.scalars().all()

    dashboard = []

    for subject in subjects:

        student_count = await db.scalar(
            select(func.count(Student.id)).where(
                Student.class_id == subject.class_id
            )
        )

        active_recordings = [
            r
            for r in subject.recordings
            if not r.is_archived
        ]

        recording_count = len(active_recordings)

        scores = [
            r.report.overall_score
            for r in active_recordings
            if r.report and r.report.overall_score is not None
        ]
        average_score = round(sum(scores) / len(scores), 1) if scores else None

        if recording_count == 0:
            status = "No Recording"

        elif any(
            r.status.value in ["processing", "pending"]
            for r in active_recordings
        ):
            status = "Pending"

        elif any(
            r.status.value == "failed"
            for r in active_recordings
        ):
            status = "Failed"

        else:
            status = "Completed"

        dashboard.append(
            TeacherDashboardSubjectOut(
                subject_id=subject.id,
                subject_name=subject.name,
                class_name=subject.class_.name,
                section=subject.class_.section,
                academic_year="2026-27",
                student_count=student_count,
                recording_count=recording_count,
                ai_status=status,
                average_score=average_score,
            )
        )

    return TeacherDashboardOut(
        subjects=dashboard
    )
    


@router.delete(
    "/chapters/{chapter_id}",
    status_code=204,
)
async def delete_teacher_chapter(
    chapter_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    chapter = await chapter_service.get_teacher_owned_chapter(
        db,
        chapter_id,
        teacher.id,
    )
    recording_count = await db.scalar(
        select(func.count(Recording.id)).where(
            Recording.chapter_id == chapter.id,
        )
    )
    if recording_count and recording_count > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a chapter that contains recordings.",
        )

    await db.delete(chapter)
    await db.commit()
    return None

@router.get("/subjects/{subject_id}", response_model=SubjectWithClassOut)
async def get_my_subject(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    subject = await chapter_service.get_teacher_owned_subject(db,
    subject_id,
    teacher.id,)
    return subject


@router.get("/subjects/{subject_id}/chapters", response_model=list[ChapterOut])
async def list_subject_chapters(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    from app.models.chapter import Chapter
    from app.models.recording import Recording
    from app.schemas.chapter import ChapterOut
    
    await chapter_service.get_teacher_owned_subject(
        db,
        subject_id,
        teacher.id,
    )
    
    result = await db.execute(
        select(Chapter)
        .options(selectinload(Chapter.documents))
        .where(Chapter.subject_id == subject_id)
        .order_by(Chapter.created_at.asc())
    )
    chapters = list(result.scalars().all())

    rec_result = await db.execute(
        select(Recording)
        .options(selectinload(Recording.report))
        .where(
            Recording.subject_id == subject_id,
            Recording.is_archived == False,
        )
    )
    all_recordings = rec_result.scalars().all()

    out = []
    for c in chapters:
        recs = [
            r
            for r in all_recordings
            if r.chapter_id == c.id
            or (
                r.chapter_id is None
                and r.chapter_name
                and r.chapter_name.strip().lower() == c.name.strip().lower()
            )
            or (
                r.chapter_id is None
                and not r.chapter_name
                and len(chapters) == 1
            )
        ]

        if not recs:
            status = "No Recording"
        elif any(r.status.value in ["pending", "processing"] for r in recs):
            status = "Pending"
        elif any(r.status.value == "failed" for r in recs):
            status = "Failed"
        else:
            status = "Completed"

        scores = [
            r.report.overall_score
            for r in recs
            if r.report is not None and r.report.overall_score is not None
        ]

        c_out = ChapterOut.model_validate(c)
        c_out.ai_status = status
        c_out.average_score = round(sum(scores) / len(scores), 1) if scores else None
        out.append(c_out)

    return out


@router.post(
    "/subjects/{subject_id}/chapters",
    response_model=ChapterOut,
    status_code=201,
)
async def create_subject_chapter(
    subject_id: int,
    body: ChapterCreate,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    # Verify the subject belongs to the logged-in teacher
    await chapter_service.get_teacher_owned_subject(db,
    subject_id,
    teacher.id,)

    from app.models.learning_plan import LearningPlan

    # Check if a chapter with this name already exists in the subject
    existing_res = await db.execute(
        select(Chapter).where(
            Chapter.subject_id == subject_id,
            func.lower(Chapter.name) == body.name.strip().lower(),
        )
    )
    existing_chapter = existing_res.scalar_one_or_none()

    if existing_chapter:
        # Chapter already exists -> add the Display Title as a new Learning Plan entry under it
        display_title = (body.description or "").strip()
        if display_title:
            new_lp = LearningPlan(
                chapter_id=existing_chapter.id,
                topic_name=existing_chapter.name,
                sub_topic=display_title,
            )
            db.add(new_lp)
            await db.commit()
        return existing_chapter

    # Create new chapter
    chapter = Chapter(
        subject_id=subject_id,
        name=body.name.strip(),
        description=body.description,
    )
    db.add(chapter)
    await db.commit()
    await db.flush()

    display_title = (body.description or "").strip()
    if display_title:
        new_lp = LearningPlan(
            chapter_id=chapter.id,
            topic_name=chapter.name,
            sub_topic=display_title,
        )
        db.add(new_lp)
        await db.commit()

    await db.refresh(chapter)
    return chapter



@router.put(
    "/chapters/{chapter_id}",
    response_model=ChapterOut,
)
async def update_teacher_chapter(
    chapter_id: int,
    body: ChapterUpdate,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    chapter = await chapter_service.get_teacher_owned_chapter(
        db,
        chapter_id,
        teacher.id,
    )

    # Prevent duplicate names within the same subject
    if body.name is not None:

        existing = await db.execute(
            select(Chapter).where(
                Chapter.subject_id == chapter.subject_id,
                func.lower(Chapter.name) == body.name.lower(),
                Chapter.id != chapter.id,
            )
        )

        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="A chapter with this name already exists.",
            )

        chapter.name = body.name.strip()

    if body.description is not None:
        chapter.description = body.description

    await db.commit()

    await db.refresh(chapter)

    return chapter


@router.get(
    "/chapters/{chapter_id}",
    response_model=ChapterOut,
)
async def get_teacher_chapter_details(
    chapter_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    chapter = await chapter_service.get_teacher_owned_chapter(
        db,
        chapter_id,
        teacher.id,
    )

    return chapter



@router.get(
    "/chapters/{chapter_id}/documents",
    response_model=list[ChapterDocumentOut],
)
async def list_teacher_chapter_documents(
    chapter_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    chapter = await chapter_service.get_teacher_owned_chapter(
        db,
        chapter_id,
        teacher.id,
    )

    result = await db.execute(
        select(ChapterDocument)
        .where(
            ChapterDocument.chapter_id == chapter.id,
        )
        .order_by(
            ChapterDocument.uploaded_at.desc(),
        )
    )

    return list(result.scalars().all())



@router.get(
    "/chapters/{chapter_id}/knowledge",
    response_model=ChapterKnowledgeOut,
)
async def get_teacher_chapter_knowledge(
    chapter_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    await chapter_service.get_teacher_owned_chapter(
        db,
        chapter_id,
        teacher.id,
    )

    knowledge = await chapter_knowledge_service.get_chapter_knowledge(
        db,
        chapter_id,
    )

    if not knowledge:
        raise HTTPException(
            status_code=404,
            detail="Chapter knowledge profile has not been generated yet. Please upload and process documents first.",
        )

    return knowledge




@router.post(
    "/chapters/{chapter_id}/documents",
    response_model=ChapterDocumentOut,
    status_code=201,
)
async def upload_teacher_chapter_document(
    chapter_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    # Verify teacher owns this chapter
    await chapter_service.get_teacher_owned_chapter(
        db,
        chapter_id,
        teacher.id,
    )

    # Upload to the same storage location
    s3_result = await upload_document(
        file,
        folder=f"classecho/chapters/{chapter_id}/documents",
    )

    # Create document record
    doc = ChapterDocument(
        chapter_id=chapter_id,
        file_name=file.filename or "unknown",
        storage_url=s3_result["url"],
        file_type=s3_result["file_type"],
        processing_status="PENDING",
    )

    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    # Trigger existing AI pipeline
    from app.tasks.document_tasks import process_chapter_document

    process_chapter_document.delay(doc.id)

    return doc



@router.delete(
    "/chapters/documents/{document_id}",
    status_code=204,
)
async def delete_teacher_chapter_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    result = await db.execute(
        select(ChapterDocument)
        .join(Chapter, ChapterDocument.chapter_id == Chapter.id)
        .join(Subject, Chapter.subject_id == Subject.id)
        .where(
            ChapterDocument.id == document_id,
            Subject.teacher_id == teacher.id,
        )
    )

    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    await db.delete(doc)
    await db.commit()



@router.get("/subjects/{subject_id}/recordings", response_model=list[RecordingWithReport])
async def list_recordings(
    subject_id: int,
    chapter_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    await chapter_service.get_teacher_owned_subject(db,
    subject_id,
    teacher.id,)
    # result = await db.execute(
    #     select(Recording)
    #     .options(
    #         selectinload(Recording.report).selectinload(LLMReport.recording),
    #         selectinload(Recording.chapter)
    #         .selectinload(Chapter.documents),
    #         selectinload(Recording.chapter)
    #         .selectinload(Chapter.knowledge),
    #     )
    #     .where(
    #         Recording.subject_id == subject_id,
    #         Recording.teacher_id == teacher.id,
    #         Recording.is_archived == False
    #     )
    #     .order_by(Recording.uploaded_at.desc())
    # )
    
    query = ( select(Recording)
        .options(
        selectinload(Recording.report).selectinload(LLMReport.recording),
        selectinload(Recording.learning_plan),
        selectinload(Recording.chapter)
        .selectinload(Chapter.documents),
        selectinload(Recording.chapter)
        .selectinload(Chapter.knowledge),
        )
        .where(
        Recording.subject_id == subject_id,
        Recording.teacher_id == teacher.id,
        Recording.is_archived == False,
        )
    )

    if chapter_id is not None:
        query = query.where(
            Recording.chapter_id == chapter_id,
        )

    result = await db.execute(
        query.order_by(
            Recording.uploaded_at.desc(),
        )
    )
    return result.scalars().all()


@router.post("/subjects/{subject_id}/recordings", response_model=RecordingWithReport, status_code=201)
async def upload_recording(
    subject_id: int,
    file: UploadFile = File(...),
    chapter_id: int = Form(...),
    learning_plan_id: int | None = Form(None),
    lecture_title: str | None = Form(None),
    description: str | None = Form(None),
    transcription: str | None = Form(None),
    duration_seconds: float | None = Form(None),
    replace_recording_id: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    from app.models.chapter import Chapter

    await chapter_service.get_teacher_owned_subject(
        db,
        subject_id,
        teacher.id,
    )

    # Validate chapter exists and belongs to the subject
    res = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id, Chapter.subject_id == subject_id)
    )
    chapter_obj = res.scalar_one_or_none()
    if not chapter_obj:
        raise HTTPException(
            status_code=404,
            detail="Chapter not found or does not belong to this subject"
        )

    mime = _resolve_mime(file)
    if mime not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{file.content_type}'. Upload an audio file.",
        )

    upload_result = await upload_audio(file, subject_id, content_type=mime)

    # Check if this replaces an existing recording (explicit ID or matching learning plan)
    existing_recording = None
    if replace_recording_id:
        res_rec = await db.execute(
            select(Recording).where(
                Recording.id == replace_recording_id,
                Recording.teacher_id == teacher.id,
            )
        )
        existing_recording = res_rec.scalar_one_or_none()
    elif learning_plan_id:
        res_rec = await db.execute(
            select(Recording).where(
                Recording.learning_plan_id == learning_plan_id,
                Recording.teacher_id == teacher.id,
                Recording.is_archived == False,
            )
        )
        existing_recording = res_rec.scalar_one_or_none()

    if existing_recording:
        # Delete old LLM report so a fresh report is generated for new audio
        report_res = await db.execute(
            select(LLMReport).where(LLMReport.recording_id == existing_recording.id)
        )
        old_report = report_res.scalar_one_or_none()
        if old_report:
            await db.delete(old_report)

        existing_recording.chapter_id = chapter_id
        # Only re-point the lecture link when one was actually supplied.
        # Assigning unconditionally wiped the association whenever a recording
        # was replaced without passing its learning_plan_id along.
        if learning_plan_id is not None:
            existing_recording.learning_plan_id = learning_plan_id
        existing_recording.chapter_name = chapter_obj.name
        if lecture_title:
            existing_recording.lecture_title = lecture_title
        if description is not None:
            existing_recording.description = description or None
        existing_recording.transcription = transcription or None
        existing_recording.transcript_segments = None
        existing_recording.cloudinary_url = upload_result["url"]
        existing_recording.cloudinary_public_id = upload_result["public_id"]
        existing_recording.duration_seconds = (
            duration_seconds if duration_seconds is not None else upload_result.get("duration")
        )
        existing_recording.status = RecordingStatus.pending
        existing_recording.error_reason = None
        existing_recording.processing_step = None
        existing_recording.processed_at = None
        existing_recording.uploaded_at = func.now()

        # Clean up any other duplicate recordings for this learning plan to ensure strictly 1 audio
        if learning_plan_id:
            dup_res = await db.execute(
                select(Recording).where(
                    Recording.learning_plan_id == learning_plan_id,
                    Recording.teacher_id == teacher.id,
                    Recording.id != existing_recording.id,
                    Recording.is_archived == False,
                )
            )
            for dup in dup_res.scalars().all():
                await db.delete(dup)

        recording = existing_recording
    else:
        # If learning_plan_id is supplied, clean up any existing recordings for that plan
        if learning_plan_id:
            dup_res = await db.execute(
                select(Recording).where(
                    Recording.learning_plan_id == learning_plan_id,
                    Recording.teacher_id == teacher.id,
                    Recording.is_archived == False,
                )
            )
            for dup in dup_res.scalars().all():
                await db.delete(dup)

        recording = Recording(
            subject_id=subject_id,
            chapter_id=chapter_id,
            learning_plan_id=learning_plan_id,
            teacher_id=teacher.id,
            chapter_name=chapter_obj.name,
            lecture_title=lecture_title or None,
            description=description or None,
            transcription=transcription or None,
            cloudinary_url=upload_result["url"],
            cloudinary_public_id=upload_result["public_id"],
            duration_seconds=(
                duration_seconds if duration_seconds is not None else upload_result.get("duration")
            ),
        )
        db.add(recording)

    await db.commit()

    # Enqueue async LLM processing
    await _queue_analysis(db, recording)

    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.report).selectinload(LLMReport.recording),
            selectinload(Recording.learning_plan),
            selectinload(Recording.chapter)
            .selectinload(Chapter.documents),
            selectinload(Recording.chapter)
            .selectinload(Chapter.knowledge),
        )
        .where(Recording.id == recording.id)
    )
    return result.scalar_one()


@router.get("/recordings/{recording_id}", response_model=RecordingWithReport)
async def get_teacher_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.teacher),
            selectinload(Recording.subject),
            selectinload(Recording.report).selectinload(LLMReport.recording),
            selectinload(Recording.report).selectinload(LLMReport.feedback),
            selectinload(Recording.report).selectinload(LLMReport.self_rating),
            selectinload(Recording.learning_plan),
            selectinload(Recording.chapter)
            .selectinload(Chapter.documents),
            selectinload(Recording.chapter)
            .selectinload(Chapter.knowledge),
        )
        .where(Recording.id == recording_id, Recording.teacher_id == teacher.id)
    )
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    # The teacher opening a finished report is the report open-rate metric.
    if recording.report and recording.report.first_opened_at is None:
        recording.report.first_opened_at = datetime.utcnow()
        await db.commit()
    return recording


@router.put("/recordings/{recording_id}/self-rating", response_model=SelfRatingOut, status_code=201)
async def put_self_rating(
    recording_id: int,
    body: SelfRatingIn,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    """The teacher's own view before the report is revealed, or a one-tap skip.

    Given once per report: after the reveal it would no longer be the
    teacher's unprompted view.
    """
    report = await _own_report(db, recording_id, teacher.id)
    existing = await db.execute(select(SelfRating).where(SelfRating.report_id == report.id))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Already rated")
    rating = SelfRating(
        report_id=report.id,
        teacher_id=teacher.id,
        ratings=body.ratings,
        reflection=(body.reflection or "").strip() or None,
        skipped=body.skipped,
    )
    db.add(rating)
    await db.commit()
    await db.refresh(rating)
    return rating


@router.put("/recordings/{recording_id}/trends", response_model=TrendsExclusionOut)
async def set_trends_exclusion(
    recording_id: int,
    body: TrendsExclusionIn,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    """Leave a lecture out of My Growth trends, or put it back."""
    report = await growth_service.set_excluded(db, recording_id, teacher.id, body.excluded)
    return TrendsExclusionOut(excluded_from_trends=report.excluded_from_trends)


@router.get("/growth", response_model=GrowthOut)
async def get_my_growth(
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    """Trends per area and progress on the current focus, against the teacher's own past only."""
    return await growth_service.build_growth(db, teacher.id)


@router.post("/recordings/{recording_id}/clip-plays", status_code=204)
async def record_clip_play(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    """Count a clip played from the report (the clip-usage metric)."""
    report = await _own_report(db, recording_id, teacher.id)
    await db.execute(
        update(LLMReport).where(LLMReport.id == report.id).values(clip_plays=LLMReport.clip_plays + 1)
    )
    await db.commit()


async def _own_report(db: AsyncSession, recording_id: int, teacher_id: int) -> LLMReport:
    result = await db.execute(
        select(LLMReport)
        .join(Recording, Recording.id == LLMReport.recording_id)
        .where(Recording.id == recording_id, Recording.teacher_id == teacher_id)
    )
    report = result.scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.put("/recordings/{recording_id}/feedback", response_model=list[ReportFeedbackOut])
async def put_report_feedback(
    recording_id: int,
    body: ReportFeedbackIn,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    """Agree or disagree with one finding. Never changes the score."""
    report = await _own_report(db, recording_id, teacher.id)
    try:
        return await report_feedback_service.upsert_feedback(db, report, teacher.id, body)
    except report_feedback_service.FeedbackTargetError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete(
    "/recordings/{recording_id}/feedback/{target_type}/{target_key}",
    response_model=list[ReportFeedbackOut],
)
async def delete_report_feedback(
    recording_id: int,
    target_type: FeedbackTarget,
    target_key: str,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    """Undo a 👍/👎."""
    report = await _own_report(db, recording_id, teacher.id)
    return await report_feedback_service.remove_feedback(db, report.id, target_type, target_key)


@router.post("/recordings/{recording_id}/retry", response_model=RecordingWithReport)
async def retry_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.report).selectinload(LLMReport.recording),
            selectinload(Recording.learning_plan),
            selectinload(Recording.chapter)
            .selectinload(Chapter.documents),
            selectinload(Recording.chapter)
            .selectinload(Chapter.knowledge),
        )
        .where(Recording.id == recording_id, Recording.teacher_id == teacher.id)
    )
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    # Anything that isn't finished can be re-queued: a worker restart leaves
    # recordings stranded in pending/processing with no way to recover them.
    if recording.status == RecordingStatus.completed:
        raise HTTPException(
            status_code=400, detail="This recording has already been analysed"
        )

    recording.status = RecordingStatus.pending
    recording.processed_at = None
    recording.error_reason = None
    recording.processing_step = None
    await db.commit()

    await _queue_analysis(db, recording)

    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.report).selectinload(LLMReport.recording),
            selectinload(Recording.learning_plan),
            selectinload(Recording.chapter)
            .selectinload(Chapter.documents),
            selectinload(Recording.chapter)
            .selectinload(Chapter.knowledge),
        )
        .where(Recording.id == recording_id)
    )
    return result.scalar_one()


@router.get("/recordings/{recording_id}/report", response_model=LLMReportOut)
async def get_report(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.report).selectinload(LLMReport.recording),
            selectinload(Recording.learning_plan),
            selectinload(Recording.chapter)
            .selectinload(Chapter.documents),
            selectinload(Recording.chapter)
            .selectinload(Chapter.knowledge),
        )
        .where(Recording.id == recording_id, Recording.teacher_id == teacher.id)
    )
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    if not recording.report:
        raise HTTPException(status_code=404, detail="Report not yet available")
    return recording.report


@router.patch("/recordings/{recording_id}", response_model=RecordingWithReport)
async def update_recording(
    recording_id: int,
    body: RecordingUpdate,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.report).selectinload(LLMReport.recording),
            selectinload(Recording.learning_plan),
            selectinload(Recording.chapter)
            .selectinload(Chapter.documents),
            selectinload(Recording.chapter)
            .selectinload(Chapter.knowledge),
        )
        .where(Recording.id == recording_id, Recording.teacher_id == teacher.id)
    )
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(recording, field, value)

    await db.commit()
    await db.refresh(recording)
    return recording


@router.post("/recordings/{recording_id}/archive", response_model=RecordingWithReport)
async def archive_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.report).selectinload(LLMReport.recording),
            selectinload(Recording.learning_plan),
            selectinload(Recording.chapter)
            .selectinload(Chapter.documents),
            selectinload(Recording.chapter)
            .selectinload(Chapter.knowledge),
        )
        .where(Recording.id == recording_id, Recording.teacher_id == teacher.id)
    )
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    recording.is_archived = True
    await db.commit()
    await db.refresh(recording)
    return recording


@router.delete("/recordings/{recording_id}", status_code=204)
async def delete_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    result = await db.execute(
        select(Recording)
        .where(Recording.id == recording_id, Recording.teacher_id == teacher.id)
    )
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    await db.delete(recording)
    await db.commit()
    return None




# async def _get_teacher_chapter(
#     chapter_id: int,
#     teacher_id: int,
#     db: AsyncSession,
# ) -> Chapter:
#     result = await db.execute(
#         select(Chapter)
#         .join(Subject, Subject.id == Chapter.subject_id)
#         .where(
#             Chapter.id == chapter_id,
#             Subject.teacher_id == teacher_id,
#         )
#     )

#     chapter = result.scalar_one_or_none()

#     if not chapter:
#         raise HTTPException(
#             status_code=404,
#             detail="Chapter not found or not assigned to you",
#         )

#     return chapter



# async def _get_teacher_subject(subject_id: int, teacher_id: int, db: AsyncSession) -> Subject:
#     result = await db.execute(
#         select(Subject)
#         .options(selectinload(Subject.teacher), selectinload(Subject.class_))
#         .where(Subject.id == subject_id, Subject.teacher_id == teacher_id)
#     )
#     subject = result.scalar_one_or_none()
#     if not subject:
#         raise HTTPException(status_code=404, detail="Subject not found or not assigned to you")
#     return subject
