from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import datetime

from app.database import get_db
from app.core.deps import get_school_admin
from app.models.class_ import Class
from app.models.school import School
from app.models.school_admin import SchoolAdmin
from app.models.subject import Subject
from app.models.teacher import Teacher
from app.models.student import Student
from app.models.recording import Recording
from app.models.llm_report import LLMReport
from app.schemas.class_ import ClassCreate, ClassUpdate, ClassOut
from app.schemas.school import SchoolOut
from app.schemas.school_admin import SchoolAdminProfileOut, SchoolAdminUpdate
from app.schemas.subject import SubjectCreate, SubjectUpdate, SubjectOut, AssignTeacherRequest, SubjectWithClassOut
from app.schemas.teacher import TeacherCreate, TeacherOut, TeacherProfileOut
from app.schemas.recording import RecordingWithReport, LLMReportOut
from app.schemas.student import StudentOut, StudentWithClassOut
from app.schemas.chapter import ChapterCreate, ChapterUpdate, ChapterOut, ChapterDocumentOut, ChapterKnowledgeOut
from app.schemas.school_dashboard import (
    SchoolDashboardOverviewOut,
    SchoolAnalyticsOut,
    TeacherRankingOut,
    RecentActivityOut,
)
from app.schemas.teacher_analytics import TeacherAnalyticsOut
from app.schemas.teacher_recordings import TeacherRecordingOut
from app.core.security import hash_password
from app.services.s3_service import upload_image, upload_document
from app.services import chapter_service
from app.services.school_dashboard_service import (
    get_school_dashboard_overview,
    get_school_analytics,
    get_teacher_ranking,
    get_recent_activity,
)
from app.services.teacher_analytics_service import get_teacher_analytics
from app.services.teacher_recordings_service import (
    get_teacher_recordings,
)
from app.services.recording_detail_service import (
    get_recording_detail,
)
from app.models.chapter_document import ChapterDocument
from app.services.teacher_analytics_filters_service import (
    get_teacher_analytics_filters,
    AnalyticsFiltersOut,
)
from app.services.teacher_profile_service import get_teacher_profile

router = APIRouter()


@router.get("/me", response_model=SchoolAdminProfileOut)
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    admin = await _get_school_admin_with_school(school_admin.id, db)
    return _build_school_admin_profile(admin)


@router.put("/me", response_model=SchoolAdminProfileOut)
async def update_my_profile(
    body: SchoolAdminUpdate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    payload = body.model_dump(exclude_unset=True)
    school = await db.get(School, school_admin.school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    if "name" in payload:
        school_admin.name = payload["name"]
    if "school_name" in payload:
        school.name = payload["school_name"]
    if "school_address" in payload:
        school.address = payload["school_address"]

    await db.commit()
    admin = await _get_school_admin_with_school(school_admin.id, db)
    return _build_school_admin_profile(admin)


@router.post("/profile-image", response_model=SchoolAdminProfileOut)
async def upload_profile_image(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    result = await upload_image(file, folder=f"classecho/school-admins/{school_admin.id}")
    school_admin.profile_pic_url = result["url"]
    await db.commit()
    admin = await _get_school_admin_with_school(school_admin.id, db)
    return _build_school_admin_profile(admin)


@router.post("/logo", response_model=SchoolOut)
async def upload_school_logo(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    result = await upload_image(file, folder=f"classecho/schools/{school_admin.school_id}")
    school = await db.get(School, school_admin.school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    school.logo_url = result["url"]
    await db.commit()
    result = await db.execute(
        select(School)
        .options(selectinload(School.admin))
        .where(School.id == school.id)
    )
    return result.scalar_one()



@router.get(
    "/dashboard/overview",
    response_model=SchoolDashboardOverviewOut,
)
async def dashboard_overview(
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    return await get_school_dashboard_overview(
        db,
        school_admin.school_id,
    )


@router.get(
    "/dashboard/analytics",
    response_model=SchoolAnalyticsOut,
)
async def dashboard_analytics(
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    return await get_school_analytics(
        db,
        school_admin.school_id,
    )


@router.get(
    "/dashboard/teacher-ranking",
    response_model=TeacherRankingOut,
)
async def dashboard_teacher_ranking(
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    return await get_teacher_ranking(
        db,
        school_admin.school_id,
    )


@router.get(
    "/dashboard/recent-activity",
    response_model=RecentActivityOut,
)
async def dashboard_recent_activity(
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    return await get_recent_activity(
        db,
        school_admin.school_id,
    )



# ─── Teacher management ───────────────────────────────────────────────────────

@router.get("/teachers", response_model=list[TeacherOut])
async def list_teachers(
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    result = await db.execute(
        select(Teacher)
        .options(selectinload(Teacher.school))
        .where(Teacher.school_id == school_admin.school_id)
        .order_by(Teacher.name)
    )
    teachers = result.scalars().all()
    return [
        TeacherOut(
            id=t.id,
            name=t.name,
            email=t.email,
            profile_image_url=t.profile_image_url,
            school_id=t.school_id,
            school_name=t.school.name if t.school else None,
            created_at=t.created_at,
        )
        for t in teachers
    ]


@router.post("/teachers", response_model=TeacherOut, status_code=201)
async def create_teacher(
    body: TeacherCreate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    from app.models.admin import Admin
    from app.models.school_admin import SchoolAdmin
    from app.models.student import Student
    for Model in (Admin, SchoolAdmin, Teacher, Student):
        r = await db.execute(select(Model).where(Model.email == body.email))
        if r.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already registered")

    teacher = Teacher(
        name=body.name,
        email=body.email,
        hashed_password=hash_password(body.password),
        profile_image_url=body.profile_image_url,
        school_id=school_admin.school_id,
    )
    db.add(teacher)
    await db.commit()

    result = await db.execute(
        select(Teacher).options(selectinload(Teacher.school)).where(Teacher.id == teacher.id)
    )
    t = result.scalar_one()
    return TeacherOut(
        id=t.id,
        name=t.name,
        email=t.email,
        profile_image_url=t.profile_image_url,
        school_id=t.school_id,
        school_name=t.school.name if t.school else None,
        created_at=t.created_at,
    )

@router.get(
    "/teachers/{teacher_id}/subjects",
    response_model=list[SubjectWithClassOut],
)
async def get_teacher_subjects(
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    result = await db.execute(
        select(Subject)
        .options(
            selectinload(Subject.teacher),
            selectinload(Subject.class_),
        )
        .join(Class, Subject.class_id == Class.id)
        .where(
            Subject.teacher_id == teacher_id,
            Class.school_id == school_admin.school_id,
        )
        .order_by(Subject.name)
    )

    return result.scalars().all()

@router.get(
    "/teachers/{teacher_id}/profile",
    response_model=TeacherProfileOut,
)
async def teacher_profile(
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    result = await db.execute(
        select(Teacher).where(
            Teacher.id == teacher_id,
            Teacher.school_id == school_admin.school_id,
        )
    )

    teacher = result.scalar_one_or_none()

    if teacher is None:
        raise HTTPException(
            status_code=404,
            detail="Teacher not found",
        )

    return await get_teacher_profile(
        db=db,
        teacher_id=teacher_id,
    )

@router.get("/teachers/{teacher_id}/analytics", response_model=TeacherAnalyticsOut)
# async def teacher_analytics(
#     teacher_id: int,
#     db: AsyncSession = Depends(get_db),
#     school_admin=Depends(get_school_admin),
# ):
async def teacher_analytics(
    teacher_id: int,

    class_id: int | None = Query(None),
    subject_id: int | None = Query(None),
    semester: int | None = Query(None),
    status: str | None = Query(None),

    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    # Verify teacher belongs to this school
    result = await db.execute(
        select(Teacher).where(
            Teacher.id == teacher_id,
            Teacher.school_id == school_admin.school_id,
        )
    )
    teacher = result.scalar_one_or_none()
    if teacher is None:
        raise HTTPException(
            status_code=404,
            detail="Teacher not found",
        )
    # return await get_teacher_analytics(
    #     db,
    #     teacher_id,
    # )
    return await get_teacher_analytics(
    db=db,
    teacher_id=teacher_id,

    class_id=class_id,
    subject_id=subject_id,
    semester=semester,
    status=status,
    search=search,
    start_date=start_date,
    end_date=end_date,
)
    
@router.get(
    "/teachers/{teacher_id}/analytics/filters",
    response_model=AnalyticsFiltersOut,
)
async def teacher_analytics_filters(
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    return await get_teacher_analytics_filters(
        db=db,
        teacher_id=teacher_id,
    )

@router.get(
    "/teachers/{teacher_id}/recordings",
    response_model=list[TeacherRecordingOut],
)
async def teacher_recordings(
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    # Verify teacher belongs to this school
    result = await db.execute(
        select(Teacher).where(
            Teacher.id == teacher_id,
            Teacher.school_id == school_admin.school_id,
        )
    )

    teacher = result.scalar_one_or_none()

    if teacher is None:
        raise HTTPException(
            status_code=404,
            detail="Teacher not found",
        )

    return await get_teacher_recordings(
        db,
        teacher_id,
    )

@router.get(
    "/recordings/{recording_id}",
    response_model=RecordingWithReport,
)
async def school_recording_detail(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    recording = await get_recording_detail(
        db,
        recording_id,
    )

    if recording is None:
        raise HTTPException(
            status_code=404,
            detail="Recording not found",
        )

    if (
        recording.teacher is None
        or recording.teacher.school_id != school_admin.school_id
    ):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to view this recording",
        )

    return recording

# ─── Classes ──────────────────────────────────────────────────────────────────

@router.get("/classes", response_model=list[ClassOut])
async def list_classes(
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    result = await db.execute(
        select(Class).where(Class.school_id == school_admin.school_id).order_by(Class.name)
    )
    return result.scalars().all()


@router.post("/classes", response_model=ClassOut, status_code=201)
async def create_class(
    body: ClassCreate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    class_ = Class(
        name=body.name,
        profile_image_url=body.profile_image_url,
        school_id=school_admin.school_id,
    )
    db.add(class_)
    await db.commit()
    await db.refresh(class_)
    return class_


@router.get("/classes/{class_id}", response_model=ClassOut)
async def get_class(
    class_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    class_ = await _get_owned_class(class_id, school_admin.school_id, db)
    return class_


@router.put("/classes/{class_id}", response_model=ClassOut)
async def update_class(
    class_id: int,
    body: ClassUpdate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    class_ = await _get_owned_class(class_id, school_admin.school_id, db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(class_, field, value)
    await db.commit()
    await db.refresh(class_)
    return class_


@router.delete("/classes/{class_id}", status_code=204)
async def delete_class(
    class_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    class_ = await _get_owned_class(class_id, school_admin.school_id, db)
    await db.delete(class_)
    await db.commit()


# ─── Subjects ─────────────────────────────────────────────────────────────────

@router.get("/classes/{class_id}/subjects", response_model=list[SubjectOut])
async def list_subjects(
    class_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    await _get_owned_class(class_id, school_admin.school_id, db)
    result = await db.execute(
        select(Subject)
        .options(selectinload(Subject.teacher))
        .where(Subject.class_id == class_id)
        .order_by(Subject.name)
    )
    return result.scalars().all()


@router.post("/classes/{class_id}/subjects", response_model=SubjectOut, status_code=201)
async def create_subject(
    class_id: int,
    body: SubjectCreate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    await _get_owned_class(class_id, school_admin.school_id, db)
    subject = Subject(
        name=body.name,
        profile_image_url=body.profile_image_url,
        class_id=class_id,
    )
    db.add(subject)
    await db.commit()

    result = await db.execute(
        select(Subject).options(selectinload(Subject.teacher)).where(Subject.id == subject.id)
    )
    return result.scalar_one()


@router.put("/subjects/{subject_id}", response_model=SubjectOut)
async def update_subject(
    subject_id: int,
    body: SubjectUpdate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    subject = await _get_owned_subject(subject_id, school_admin.school_id, db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(subject, field, value)
    await db.commit()

    result = await db.execute(
        select(Subject).options(selectinload(Subject.teacher)).where(Subject.id == subject.id)
    )
    return result.scalar_one()


@router.put("/subjects/{subject_id}/assign-teacher", response_model=SubjectOut)
async def assign_teacher(
    subject_id: int,
    body: AssignTeacherRequest,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    subject = await _get_owned_subject(subject_id, school_admin.school_id, db)

    teacher = await db.get(Teacher, body.teacher_id)
    if not teacher or teacher.school_id != school_admin.school_id:
        raise HTTPException(status_code=404, detail="Teacher not found")

    # Update assignment only if teacher changed
    if subject.teacher_id != body.teacher_id:
        subject.teacher_id = body.teacher_id
        subject.teacher_assigned_at = datetime.utcnow()

    await db.commit()
    await db.refresh(subject)

    result = await db.execute(
        select(Subject)
        .options(
            selectinload(Subject.teacher),
            selectinload(Subject.class_),
        )
        .where(Subject.id == subject.id)
    )

    return result.scalar_one()

# ─── Students ────────────────────────────────────────────────────────────────

@router.get("/students", response_model=list[StudentWithClassOut])
async def list_students(
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    result = await db.execute(
        select(Student)
        .options(selectinload(Student.class_))
        .where(Student.school_id == school_admin.school_id)
        .order_by(Student.name)
    )
    students = result.scalars().all()
    return [
        StudentWithClassOut(
            id=s.id,
            name=s.name,
            email=s.email,
            profile_image_url=s.profile_image_url,
            mobile_number=s.mobile_number,
            class_id=s.class_id,
            class_name=s.class_.name,
            created_at=s.created_at,
        )
        for s in students
    ]


@router.get("/subjects/{subject_id}/students", response_model=list[StudentOut])
async def list_subject_students(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    subject = await _get_owned_subject(subject_id, school_admin.school_id, db)
    result = await db.execute(
        select(Student)
        .where(Student.class_id == subject.class_id)
        .order_by(Student.name)
    )
    return result.scalars().all()


# ─── Recordings & Reports (view) ──────────────────────────────────────────────

@router.get("/subjects/{subject_id}/recordings", response_model=list[RecordingWithReport])
async def list_subject_recordings(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    await _get_owned_subject(subject_id, school_admin.school_id, db)
    result = await db.execute(
        select(Recording)
        .options(selectinload(Recording.report))
        .where(Recording.subject_id == subject_id)
        .order_by(Recording.uploaded_at.desc())
    )
    return result.scalars().all()


@router.get("/subjects/{subject_id}/reports", response_model=list[LLMReportOut])
async def list_subject_reports(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    await _get_owned_subject(subject_id, school_admin.school_id, db)
    result = await db.execute(
        select(LLMReport)
        .join(Recording, LLMReport.recording_id == Recording.id)
        .where(Recording.subject_id == subject_id)
        .order_by(LLMReport.created_at.desc())
    )
    return result.scalars().all()


# ─── Chapters ─────────────────────────────────────────────────────────────────

@router.post("/subjects/{subject_id}/chapters", response_model=ChapterOut, status_code=201)
async def create_chapter(
    subject_id: int,
    body: ChapterCreate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    return await chapter_service.create_chapter(db, subject_id, school_admin.school_id, body)


@router.get("/subjects/{subject_id}/chapters", response_model=list[ChapterOut])
async def list_chapters(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    return await chapter_service.list_chapters(db, subject_id, school_admin.school_id)


@router.get("/chapters/{chapter_id}", response_model=ChapterOut)
async def get_chapter(
    chapter_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    return await chapter_service.get_chapter_details(db, chapter_id, school_admin.school_id)


@router.put("/chapters/{chapter_id}", response_model=ChapterOut)
async def update_chapter(
    chapter_id: int,
    body: ChapterUpdate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    return await chapter_service.update_chapter(db, chapter_id, school_admin.school_id, body)


@router.delete("/chapters/{chapter_id}", status_code=204)
async def delete_chapter(
    chapter_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    await chapter_service.delete_chapter(db, chapter_id, school_admin.school_id)


@router.post("/chapters/{chapter_id}/documents", response_model=ChapterDocumentOut, status_code=201)
async def upload_chapter_document(
    chapter_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    # Verify the chapter exists and is owned by the school admin's school
    await chapter_service.get_owned_chapter(db, chapter_id, school_admin.school_id)

    # Upload file to existing S3 storage
    s3_result = await upload_document(file, folder=f"classecho/chapters/{chapter_id}/documents")

    # Create ChapterDocument record
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

    # Trigger Celery task
    from app.tasks.document_tasks import process_chapter_document
    process_chapter_document.delay(doc.id)

    return doc


@router.get("/chapters/{chapter_id}/documents", response_model=list[ChapterDocumentOut])
async def list_chapter_documents(
    chapter_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    # Verify ownership of the chapter
    await chapter_service.get_owned_chapter(db, chapter_id, school_admin.school_id)
    
    result = await db.execute(
        select(ChapterDocument)
        .where(ChapterDocument.chapter_id == chapter_id)
        .order_by(ChapterDocument.uploaded_at.desc())
    )
    return list(result.scalars().all())


@router.delete("/chapters/documents/{document_id}", status_code=204)
async def delete_chapter_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    # Retrieve the document with ownership validation
    result = await db.execute(
        select(ChapterDocument)
        .join(Chapter, ChapterDocument.chapter_id == Chapter.id)
        .join(Subject, Chapter.subject_id == Subject.id)
        .join(Class, Subject.class_id == Class.id)
        .where(ChapterDocument.id == document_id, Class.school_id == school_admin.school_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    await db.delete(doc)
    await db.commit()


@router.get("/chapters/{chapter_id}/knowledge", response_model=ChapterKnowledgeOut)
async def get_chapter_knowledge(
    chapter_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    # Verify ownership
    await chapter_service.get_owned_chapter(db, chapter_id, school_admin.school_id)

    from app.services import chapter_knowledge_service
    knowledge = await chapter_knowledge_service.get_chapter_knowledge(db, chapter_id)
    if not knowledge:
        raise HTTPException(
            status_code=404,
            detail="Chapter knowledge profile has not been generated yet. Please upload and process documents first."
        )
    return knowledge


@router.post("/chapters/{chapter_id}/knowledge/regenerate", status_code=202)
async def regenerate_chapter_knowledge(
    chapter_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_school_admin),
):
    # Verify ownership
    await chapter_service.get_owned_chapter(db, chapter_id, school_admin.school_id)

    from app.tasks.document_tasks import generate_chapter_knowledge_task
    generate_chapter_knowledge_task.delay(chapter_id)
    return {"message": "Chapter knowledge generation task started."}



# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _get_owned_class(class_id: int, school_id: int, db: AsyncSession) -> Class:
    result = await db.execute(select(Class).where(Class.id == class_id))
    class_ = result.scalar_one_or_none()
    if not class_ or class_.school_id != school_id:
        raise HTTPException(status_code=404, detail="Class not found")
    return class_


async def _get_owned_subject(subject_id: int, school_id: int, db: AsyncSession) -> Subject:
    result = await db.execute(
        select(Subject)
        .join(Class, Subject.class_id == Class.id)
        .where(Subject.id == subject_id, Class.school_id == school_id)
    )
    subject = result.scalar_one_or_none()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject


async def _get_school_admin_with_school(admin_id: int, db: AsyncSession) -> SchoolAdmin:
    result = await db.execute(
        select(SchoolAdmin)
        .options(selectinload(SchoolAdmin.school))
        .where(SchoolAdmin.id == admin_id)
    )
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=404, detail="School admin not found")
    return admin


def _build_school_admin_profile(admin: SchoolAdmin) -> SchoolAdminProfileOut:
    school = admin.school
    return SchoolAdminProfileOut(
        id=admin.id,
        name=admin.name,
        email=admin.email,
        profile_pic_url=admin.profile_pic_url,
        school_id=admin.school_id,
        school_name=school.name,
        school_logo_url=school.logo_url,
        school_address=school.address,
        created_at=admin.created_at,
    )
