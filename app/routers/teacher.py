from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.models.chapter import Chapter

from app.database import get_db
from app.core.deps import get_teacher, get_active_teacher
from app.models.subject import Subject
from app.models.recording import Recording
from app.models.llm_report import LLMReport
from app.models.student import Student
from app.schemas.subject import SubjectOut, SubjectWithClassOut
from app.schemas.recording import RecordingWithReport, LLMReportOut, RecordingUpdate
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
from fastapi import File, UploadFile
from app.models.chapter_document import ChapterDocument
from app.schemas.chapter import ChapterDocumentOut
from app.services.s3_service import upload_document
from app.tasks.llm_tasks import process_recording

from app.models.timetable_entry import TimetableEntry
from app.schemas.timetable import TimetableEntryOut
from app.services.timetable_service import build_timetable_entry_out, day_sort_index

router = APIRouter()


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


def _teacher_out(teacher) -> TeacherOut:
    return TeacherOut(
        id=teacher.id,
        name=teacher.name,
        email=teacher.email,
        profile_image_url=teacher.profile_image_url,
        school_id=teacher.school_id,
        school_name=teacher.school.name if teacher.school else None,
        created_at=teacher.created_at,
        status=teacher.status,
        email_verified=teacher.email_verified,
        rejected_reason=teacher.rejected_reason,
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
    teacher=Depends(get_teacher),
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
            selectinload(Subject.recordings),
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

        recordings = subject.recordings

        recording_count = len(
            [
                r
                for r in recordings
                if not r.is_archived
            ]
        )

        if recording_count == 0:
            status = "No Recording"

        elif any(
            r.status.value == "processing"
            for r in recordings
        ):
            status = "Processing"

        elif any(
            r.status.value == "failed"
            for r in recordings
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
    from app.schemas.chapter import ChapterOut
    
    await chapter_service.get_teacher_owned_subject(db,
    subject_id,
    teacher.id,)
    
    result = await db.execute(
        select(Chapter)
        .options(selectinload(Chapter.documents))
        .where(Chapter.subject_id == subject_id)
        .order_by(Chapter.created_at.asc())
    )
    return list(result.scalars().all())


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

    # Prevent duplicate chapter names in the same subject
    existing = await db.execute(
        select(Chapter).where(
            Chapter.subject_id == subject_id,
            func.lower(Chapter.name) == body.name.lower(),
        )
    )

    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="A chapter with this name already exists.",
        )

    chapter = Chapter(
        subject_id=subject_id,
        name=body.name.strip(),
        description=body.description,
    )

    db.add(chapter)

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
    lecture_title: str | None = Form(None),
    description: str | None = Form(None),
    transcription: str | None = Form(None),
    duration_seconds: float | None = Form(None),
    db: AsyncSession = Depends(get_db),
    teacher=Depends(get_active_teacher),
):
    from app.models.chapter import Chapter

    await chapter_service.get_teacher_owned_subject(db,
    subject_id,
    teacher.id,)

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

    upload_result = await upload_audio(file, subject_id)

    recording = Recording(
        subject_id=subject_id,
        chapter_id=chapter_id,
        teacher_id=teacher.id,
        chapter_name=chapter_obj.name,
        lecture_title=lecture_title or None,
        description=description or None,
        transcription=transcription or None,
        cloudinary_url=upload_result["url"],
        cloudinary_public_id=upload_result["public_id"],
        duration_seconds=duration_seconds if duration_seconds is not None else upload_result.get("duration"),
    )
    db.add(recording)
    await db.commit()

    # Enqueue async LLM processing
    process_recording.delay(recording.id, recording.cloudinary_url)

    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.report).selectinload(LLMReport.recording),
            selectinload(Recording.chapter)
            .selectinload(Chapter.documents),
            selectinload(Recording.chapter)
            .selectinload(Chapter.knowledge),
        )
        .where(Recording.id == recording.id)
    )
    return result.scalar_one()


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
    if recording.status != "failed":
        raise HTTPException(status_code=400, detail="Only failed recordings can be retried")

    recording.status = "pending"
    recording.processed_at = None
    recording.error_reason = None
    await db.commit()

    process_recording.delay(recording.id, recording.cloudinary_url)

    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.report).selectinload(LLMReport.recording),
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
