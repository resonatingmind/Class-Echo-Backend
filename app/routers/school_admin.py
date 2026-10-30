from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta

from app.database import get_db
from app.core.deps import get_school_admin, get_active_school_admin
from app.models.chapter import Chapter
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
from app.schemas.teacher import (
    TeacherOut,
    TeacherProfileOut,
    TeacherInviteRequest,
    TeacherRejectRequest,
)
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
from app.services.s3_service import upload_image, upload_document
from app.services import admin_privacy, chapter_service, growth_service
from app.schemas.growth import AdminTeacherGrowthOut, SchoolGrowthOut
from app.services.school_dashboard_service import (
    get_school_dashboard_overview,
    get_school_analytics,
    get_recent_activity,
)
from app.services.teacher_recordings_service import (
    get_teacher_recordings,
    get_chapter_recordings,
)
from app.services.recording_detail_service import (
    get_recording_detail,
)
from app.models.chapter_document import ChapterDocument
from app.services.teacher_analytics_filters_service import (
    AnalyticsFiltersOut,
)
from app.services.teacher_profile_service import get_teacher_profile
from app.schemas.lookup import ( TeacherLookupOut, ClassLookupOut, SubjectLookupOut, )
from app.schemas.class_filters import ClassFiltersOut
from app.services.class_filters_service import (
    class_name_rank,
    get_class_filters,
)

from app.schemas.quick_assignment import (
    QuickAssignmentIn,
    QuickAssignmentOut,
    ReplaceAssignmentIn,
    ReplaceAssignmentOut,
)

from app.services.planner_assignment_service import (
    quick_assignment,
    replace_assignment,
)
from app.services import teacher_onboarding_service as onboarding
from app.services import invite_service
from app.config import settings

from fastapi.responses import StreamingResponse
from app.models.timetable_entry import TimetableEntry
from app.schemas.timetable import (
    TimetableEntryCreate,
    TimetableEntryUpdate,
    TimetableEntryOut,
    TimetableImportResult,
)
from app.services.timetable_service import (
    build_timetable_entry_out,
    build_template_workbook,
    import_timetable_from_excel,
    day_sort_index,
    _str_to_time,
)

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
    if payload.get("streak_min_recordings") is not None:
        school.streak_min_recordings = payload["streak_min_recordings"]
    if "school_address" in payload:
        school.address = payload["school_address"]

    await db.commit()
    admin = await _get_school_admin_with_school(school_admin.id, db)
    return _build_school_admin_profile(admin)


@router.post("/profile-image", response_model=SchoolAdminProfileOut)
async def upload_profile_image(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
):
    # Teachers are compared only with their own past; see GET /school/growth.
    raise HTTPException(status_code=410, detail="Teacher ranking was removed. See each teacher's growth instead.")


@router.get("/growth", response_model=SchoolGrowthOut)
async def school_growth(
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    """Each teacher's status against their own past, and the school's common weak areas. No ranking."""
    return await growth_service.school_growth(db, school_admin.school_id)


@router.get(
    "/dashboard/recent-activity",
    response_model=RecentActivityOut,
)
async def dashboard_recent_activity(
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    return await get_recent_activity(
        db,
        school_admin.school_id,
    )



# ─── Teacher management ───────────────────────────────────────────────────────

def _teacher_admin_out(t: Teacher) -> TeacherOut:
    return TeacherOut(
        id=t.id,
        name=t.name,
        email=t.email,
        profile_image_url=t.profile_image_url,
        school_id=t.school_id,
        school_name=t.school.name if t.school else None,
        created_at=t.created_at,
        status=t.status,
        email_verified=t.email_verified,
        rejected_reason=t.rejected_reason,
    )


@router.get("/teachers", response_model=list[TeacherOut])
async def list_teachers(
    status: str | None = Query(None, description="Filter by invited/pending_approval/active/suspended/rejected"),
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    query = (
        select(Teacher)
        .options(selectinload(Teacher.school))
        .where(Teacher.school_id == school_admin.school_id)
    )
    if status is not None:
        query = query.where(Teacher.status == status)
    result = await db.execute(query.order_by(Teacher.name))
    teachers = result.scalars().all()
    return [_teacher_admin_out(t) for t in teachers]


@router.get("/teachers/lookup", response_model=TeacherLookupOut)
async def lookup_teacher(
    email: str,
    db: AsyncSession = Depends(get_db),
    school_admin: SchoolAdmin = Depends(get_active_school_admin),
):
    teacher = await db.scalar(
        select(Teacher).where(
            Teacher.school_id == school_admin.school_id,
            Teacher.email == email,
        )
    )

    if teacher:
        return TeacherLookupOut(
            exists=True,
            id=teacher.id,
            name=teacher.name,
            email=teacher.email,
        )

    return TeacherLookupOut(
        exists=False,
    )

@router.post("/teachers/invite", response_model=TeacherOut, status_code=201)
async def invite_teacher(
    body: TeacherInviteRequest,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    action, existing = await onboarding.classify_teacher_signup(
        db, body.email, school_admin.school_id
    )

    if action == onboarding.CONFLICT_TAKEN:
        raise HTTPException(status_code=409, detail="Email already registered")
    if action == onboarding.CONFLICT_OTHER_SCHOOL:
        raise HTTPException(
            status_code=409,
            detail="This email is already associated with a different school",
        )
    if action == onboarding.EXISTING_PENDING_SAME_SCHOOL:
        raise HTTPException(
            status_code=409,
            detail="This teacher already self-registered and is awaiting your approval",
        )

    if action == onboarding.EXISTING_INVITED_SAME_SCHOOL:
        teacher = existing
        teacher.name = body.name
    else:
        teacher = Teacher(
            name=body.name,
            email=body.email,
            hashed_password=None,
            school_id=school_admin.school_id,
            status="invited",
            invited_by_school_admin_id=school_admin.id,
        )
        db.add(teacher)

    await db.flush()

    raw_token = await invite_service.issue_invite_token(db, "teacher", teacher.id)
    await db.commit()
    await db.refresh(teacher)

    result = await db.execute(
        select(Teacher).options(selectinload(Teacher.school)).where(Teacher.id == teacher.id)
    )
    teacher = result.scalar_one()

    invite_service.queue_invite_email(
        to_email=teacher.email,
        to_name=teacher.name,
        organisation_name=teacher.school.name if teacher.school else "Class Echo",
        role_label="teacher",
        raw_token=raw_token,
        inviter_name=school_admin.name,
        school_id=teacher.school_id,
        related_type="teacher",
        related_id=teacher.id,
    )

    return _teacher_admin_out(teacher)


@router.post("/teachers/{teacher_id}/approve", response_model=TeacherOut)
async def approve_teacher(
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    teacher = await _get_owned_teacher(teacher_id, school_admin.school_id, db)
    if teacher.status != "pending_approval":
        raise HTTPException(status_code=400, detail="Only pending teachers can be approved")

    teacher.status = "active"
    teacher.approved_at = datetime.utcnow()
    teacher.approved_by_school_admin_id = school_admin.id
    await db.commit()
    await db.refresh(teacher)
    return _teacher_admin_out(teacher)


@router.post("/teachers/{teacher_id}/reject", response_model=TeacherOut)
async def reject_teacher(
    teacher_id: int,
    body: TeacherRejectRequest,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    teacher = await _get_owned_teacher(teacher_id, school_admin.school_id, db)
    if teacher.status != "pending_approval":
        raise HTTPException(status_code=400, detail="Only pending teachers can be rejected")

    teacher.status = "rejected"
    teacher.rejected_reason = body.reason
    await db.commit()
    await db.refresh(teacher)
    return _teacher_admin_out(teacher)


@router.post("/teachers/{teacher_id}/suspend", response_model=TeacherOut)
async def suspend_teacher(
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    teacher = await _get_owned_teacher(teacher_id, school_admin.school_id, db)
    if teacher.status != "active":
        raise HTTPException(status_code=400, detail="Only active teachers can be suspended")

    teacher.status = "suspended"
    await db.commit()
    await db.refresh(teacher)
    return _teacher_admin_out(teacher)


@router.post("/teachers/{teacher_id}/reactivate", response_model=TeacherOut)
async def reactivate_teacher(
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    teacher = await _get_owned_teacher(teacher_id, school_admin.school_id, db)
    if teacher.status != "suspended":
        raise HTTPException(status_code=400, detail="Only suspended teachers can be reactivated")

    teacher.status = "active"
    await db.commit()
    await db.refresh(teacher)
    return _teacher_admin_out(teacher)


async def _get_owned_teacher(teacher_id: int, school_id: int, db: AsyncSession) -> Teacher:
    result = await db.execute(
        select(Teacher)
        .options(selectinload(Teacher.school))
        .where(Teacher.id == teacher_id, Teacher.school_id == school_id)
    )
    teacher = result.scalar_one_or_none()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    return teacher


@router.get(
    "/teachers/{teacher_id}/subjects",
    response_model=list[SubjectWithClassOut],
)
async def get_teacher_subjects(
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
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
    # Per-lecture scores stay with the teacher; GET /school/teachers/{id}/growth replaces this.
    raise HTTPException(status_code=403, detail=admin_privacy.PRIVATE_DETAIL)


@router.get("/teachers/{teacher_id}/growth", response_model=AdminTeacherGrowthOut)
async def teacher_growth(
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    """A teacher's trend, focus, streak and status, plus the milestones and clips they shared."""
    teacher = (await db.execute(
        select(Teacher).where(Teacher.id == teacher_id, Teacher.school_id == school_admin.school_id)
    )).scalar_one_or_none()
    if teacher is None:
        raise HTTPException(status_code=404, detail="Teacher not found")
    growth = await growth_service.build_growth(db, teacher, shared_only=True)
    return AdminTeacherGrowthOut(
        teacher_id=teacher.id,
        name=teacher.name,
        status=growth_service.teacher_status(growth),
        growth=growth,
        shared_clips=await admin_privacy.shared_clips_for_admin(db, teacher.id),
    )


@router.get(
    "/teachers/{teacher_id}/analytics/filters",
    response_model=AnalyticsFiltersOut,
)
async def teacher_analytics_filters(
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    raise HTTPException(status_code=403, detail=admin_privacy.PRIVATE_DETAIL)

@router.get(
    "/teachers/{teacher_id}/recordings",
    response_model=list[TeacherRecordingOut],
)
async def teacher_recordings(
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
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

    return admin_privacy.private_list(await get_teacher_recordings(
        db,
        teacher_id,
    ))

@router.get(
    "/chapters/{chapter_id}/recordings",
    response_model=list[TeacherRecordingOut],
)
async def chapter_recordings(
    chapter_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    owned = await db.execute(
        select(Chapter.id)
        .join(Subject, Subject.id == Chapter.subject_id)
        .join(Class, Class.id == Subject.class_id)
        .where(Chapter.id == chapter_id, Class.school_id == school_admin.school_id)
    )
    if owned.first() is None:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return admin_privacy.private_list(await get_chapter_recordings(
        db=db,
        chapter_id=chapter_id,
    ))

@router.get(
    "/recordings/{recording_id}",
    response_model=RecordingWithReport,
)
async def school_recording_detail(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
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

    return admin_privacy.private_recording(recording)

# ─── Classes ──────────────────────────────────────────────────────────────────

@router.get("/classes", response_model=list[ClassOut])
async def list_classes(
    name: str | None = Query(None, description="Filter by class name"),
    section: str | None = Query(None, description="Filter by section"),
    teacher_id: int | None = Query(
        None, description="Only classes with a subject taught by this teacher"
    ),
    subject: str | None = Query(
        None, description="Only classes offering a subject with this name"
    ),
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    stmt = select(Class).where(Class.school_id == school_admin.school_id)

    if name:
        stmt = stmt.where(Class.name == name)

    if section:
        stmt = stmt.where(Class.section == section)

    if teacher_id is not None or subject:
        subject_stmt = select(Subject.class_id)

        if teacher_id is not None:
            subject_stmt = subject_stmt.where(Subject.teacher_id == teacher_id)

        if subject:
            subject_stmt = subject_stmt.where(Subject.name == subject)

        stmt = stmt.where(Class.id.in_(subject_stmt))

    result = await db.execute(
        stmt.order_by(class_name_rank(), Class.name, Class.section)
    )
    return result.scalars().all()


@router.get("/classes/filters", response_model=ClassFiltersOut)
async def list_class_filters(
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    return await get_class_filters(db, school_admin.school_id)


@router.get("/classes/lookup", response_model=ClassLookupOut)
async def lookup_class(
    name: str,
    section: str,
    db: AsyncSession = Depends(get_db),
    school_admin: SchoolAdmin = Depends(get_active_school_admin),
):
    class_ = await db.scalar(
        select(Class).where(
            Class.school_id == school_admin.school_id,
            Class.name == name,
            Class.section == section,
        )
    )

    if class_:
        return ClassLookupOut(
            exists=True,
            id=class_.id,
            name=class_.name,
            section=class_.section,
        )

    return ClassLookupOut(
        exists=False,
        name=name,
        section=section,
    )

@router.post("/classes", response_model=ClassOut, status_code=201)
async def create_class(
    body: ClassCreate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    
    existing = await db.scalar(
    select(Class).where(
        Class.school_id == school_admin.school_id,
        Class.name == body.name,
        Class.section == body.section,
    )
  )

    if existing:
        raise HTTPException(
            status_code=400,
            detail=f'Class "{body.name}" - Section "{body.section}" already exists.',
        )
    
    class_ = Class(
    name=body.name,
    section=body.section,
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
    school_admin=Depends(get_active_school_admin),
):
    class_ = await _get_owned_class(class_id, school_admin.school_id, db)
    return class_


@router.put("/classes/{class_id}", response_model=ClassOut)
async def update_class(
    class_id: int,
    body: ClassUpdate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
):
    class_ = await _get_owned_class(class_id, school_admin.school_id, db)
    await db.delete(class_)
    await db.commit()



# ─── PLANNER ─────────────────────────────────────────────────────────────────

@router.post(
    "/planner/quick-assignment",
    response_model=QuickAssignmentOut,
)
async def create_quick_assignment(
    body: QuickAssignmentIn,
    db: AsyncSession = Depends(get_db),
    school_admin: SchoolAdmin = Depends(get_active_school_admin),
):
    return await quick_assignment(
        db=db,
        school_admin=school_admin,
        body=body,
    )


@router.patch(
    "/planner/assignments/replace",
    response_model=ReplaceAssignmentOut,
)
async def replace_teacher_assignment(
    body: ReplaceAssignmentIn,
    db: AsyncSession = Depends(get_db),
    school_admin: SchoolAdmin = Depends(get_active_school_admin),
):
    return await replace_assignment(
        db=db,
        school_admin=school_admin,
        body=body,
    )


# ─── Subjects ─────────────────────────────────────────────────────────────────

@router.get("/classes/{class_id}/subjects", response_model=list[SubjectOut])
async def list_subjects(
    class_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
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



@router.get("/subjects/{class_id}/lookup", response_model=SubjectLookupOut)
async def lookup_subject(
    class_id: int,
    name: str,
    db: AsyncSession = Depends(get_db),
    school_admin: SchoolAdmin = Depends(get_active_school_admin),
):
    
    class_ = await db.scalar(
    select(Class).where(
        Class.id == class_id,
        Class.school_id == school_admin.school_id,
    )
)

    if class_ is None:
        raise HTTPException(
            status_code=404,
            detail="Class not found.",
        )
    
    subject = await db.scalar(
        select(Subject).where(
            Subject.class_id == class_id,
            # Subject.name == name,
            func.lower(Subject.name) == name.lower(),
        )
    )

    if subject:
        return SubjectLookupOut(
            exists=True,
            id=subject.id,
            name=subject.name,
            class_id=subject.class_id,
        )

    return SubjectLookupOut(
        exists=False,
        name=name,
        class_id=class_id,
    )

@router.put("/subjects/{subject_id}", response_model=SubjectOut)
async def update_subject(
    subject_id: int,
    body: SubjectUpdate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
):
    subject = await _get_owned_subject(subject_id, school_admin.school_id, db)

    if body.teacher_id is not None:
        teacher = await db.get(Teacher, body.teacher_id)
        if not teacher or teacher.school_id != school_admin.school_id:
            raise HTTPException(status_code=404, detail="Teacher not found")

        # Update assignment only if teacher changed
        if subject.teacher_id != body.teacher_id:
            subject.teacher_id = body.teacher_id
            subject.teacher_assigned_at = datetime.utcnow()
    else:
        subject.teacher_id = None
        subject.teacher_assigned_at = None

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


@router.delete("/subjects/{subject_id}", status_code=204)
async def delete_subject(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    subject = await _get_owned_subject(subject_id, school_admin.school_id, db)
    await db.delete(subject)
    await db.commit()
    return None


# ─── Students ────────────────────────────────────────────────────────────────

@router.get("/students", response_model=list[StudentWithClassOut])
async def list_students(
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
):
    await _get_owned_subject(subject_id, school_admin.school_id, db)
    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.report).selectinload(LLMReport.shared_clips),
            selectinload(Recording.learning_plan),
        )
        .where(Recording.subject_id == subject_id)
        .order_by(Recording.uploaded_at.desc())
    )
    return [admin_privacy.private_recording(r) for r in result.scalars().all()]


@router.get("/subjects/{subject_id}/reports", response_model=list[LLMReportOut])
async def list_subject_reports(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    await _get_owned_subject(subject_id, school_admin.school_id, db)
    raise HTTPException(status_code=403, detail=admin_privacy.PRIVATE_DETAIL)


# ─── Chapters ─────────────────────────────────────────────────────────────────

@router.post("/subjects/{subject_id}/chapters", response_model=ChapterOut, status_code=201)
async def create_chapter(
    subject_id: int,
    body: ChapterCreate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    return await chapter_service.create_chapter(db, subject_id, school_admin.school_id, body)


@router.get("/subjects/{subject_id}/chapters", response_model=list[ChapterOut])
async def list_chapters(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    return await chapter_service.list_chapters(db, subject_id, school_admin.school_id)


@router.get("/chapters/{chapter_id}", response_model=ChapterOut)
async def get_chapter(
    chapter_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    return await chapter_service.get_chapter_details(db, chapter_id, school_admin.school_id)


@router.put("/chapters/{chapter_id}", response_model=ChapterOut)
async def update_chapter(
    chapter_id: int,
    body: ChapterUpdate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    return await chapter_service.update_chapter(db, chapter_id, school_admin.school_id, body)


@router.delete("/chapters/{chapter_id}", status_code=204)
async def delete_chapter(
    chapter_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    await chapter_service.delete_chapter(db, chapter_id, school_admin.school_id)


@router.post("/chapters/{chapter_id}/documents", response_model=ChapterDocumentOut, status_code=201)
async def upload_chapter_document(
    chapter_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
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
    school_admin=Depends(get_active_school_admin),
):
    # Verify ownership
    await chapter_service.get_owned_chapter(db, chapter_id, school_admin.school_id)

    from app.tasks.document_tasks import generate_chapter_knowledge_task
    generate_chapter_knowledge_task.delay(chapter_id)
    return {"message": "Chapter knowledge generation task started."}



# ─── Helpers ──────────────────────────────────────────────────────────────────


# ─── TIMETABLE ────────────────────────────────────────────────────────────

@router.get("/timetable", response_model=list[TimetableEntryOut])
async def list_timetable(
    class_id: int | None = None,
    day_of_week: str | None = None,
    academic_year: str | None = None,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    query = (
        select(TimetableEntry)
        .options(
            selectinload(TimetableEntry.class_),
            selectinload(TimetableEntry.subject),
            selectinload(TimetableEntry.teacher),
        )
        .where(TimetableEntry.school_id == school_admin.school_id)
    )
    if class_id is not None:
        query = query.where(TimetableEntry.class_id == class_id)
    if day_of_week is not None:
        query = query.where(TimetableEntry.day_of_week == day_of_week.capitalize())
    if academic_year is not None:
        query = query.where(TimetableEntry.academic_year == academic_year)

    result = await db.execute(query)
    entries = sorted(
        result.scalars().all(),
        key=lambda e: (day_sort_index(e.day_of_week), e.period_number),
    )
    return [build_timetable_entry_out(e) for e in entries]


@router.get("/timetable/template")
async def download_timetable_template():
    # Intentionally unauthenticated: this is a static, blank example file
    # with no school data, so it can be opened directly as a download link.
    buffer = build_template_workbook()
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=timetable_template.xlsx"},
    )


@router.post("/timetable/import", response_model=TimetableImportResult)
async def import_timetable(
    file: UploadFile = File(...),
    replace_existing: bool = False,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return await import_timetable_from_excel(
        db, school_admin.school_id, content, replace_existing
    )


async def _get_owned_teacher_for_timetable(
    teacher_id: int, school_id: int, db: AsyncSession
) -> Teacher:
    result = await db.execute(select(Teacher).where(Teacher.id == teacher_id))
    teacher = result.scalar_one_or_none()
    if not teacher or teacher.school_id != school_id:
        raise HTTPException(status_code=404, detail="Teacher not found")
    return teacher


async def _check_timetable_clashes(
    db: AsyncSession,
    class_id: int,
    teacher_id: int,
    day_of_week: str,
    period_number: int,
    academic_year: str,
    exclude_entry_id: int | None = None,
) -> None:
    class_clash_query = select(TimetableEntry).where(
        TimetableEntry.class_id == class_id,
        TimetableEntry.day_of_week == day_of_week,
        TimetableEntry.period_number == period_number,
        TimetableEntry.academic_year == academic_year,
    )
    teacher_clash_query = select(TimetableEntry).where(
        TimetableEntry.teacher_id == teacher_id,
        TimetableEntry.day_of_week == day_of_week,
        TimetableEntry.period_number == period_number,
        TimetableEntry.academic_year == academic_year,
    )
    if exclude_entry_id is not None:
        class_clash_query = class_clash_query.where(TimetableEntry.id != exclude_entry_id)
        teacher_clash_query = teacher_clash_query.where(TimetableEntry.id != exclude_entry_id)

    class_clash = await db.scalar(class_clash_query)
    if class_clash:
        raise HTTPException(
            status_code=400,
            detail="This class already has a period at that day and time.",
        )

    teacher_clash = await db.scalar(teacher_clash_query)
    if teacher_clash:
        raise HTTPException(
            status_code=400,
            detail="This teacher is already scheduled in another class at that day and time.",
        )


@router.post("/timetable", response_model=TimetableEntryOut, status_code=201)
async def create_timetable_entry(
    body: TimetableEntryCreate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    await _get_owned_class(body.class_id, school_admin.school_id, db)
    await _get_owned_subject(body.subject_id, school_admin.school_id, db)
    await _get_owned_teacher_for_timetable(body.teacher_id, school_admin.school_id, db)

    await _check_timetable_clashes(
        db, body.class_id, body.teacher_id, body.day_of_week,
        body.period_number, body.academic_year,
    )

    entry = TimetableEntry(
        school_id=school_admin.school_id,
        class_id=body.class_id,
        subject_id=body.subject_id,
        teacher_id=body.teacher_id,
        day_of_week=body.day_of_week,
        period_number=body.period_number,
        start_time=_str_to_time(body.start_time),
        end_time=_str_to_time(body.end_time),
        room=body.room,
        academic_year=body.academic_year,
    )
    db.add(entry)
    await db.commit()
    result = await db.execute(
        select(TimetableEntry)
        .options(
            selectinload(TimetableEntry.class_),
            selectinload(TimetableEntry.subject),
            selectinload(TimetableEntry.teacher),
        )
        .where(TimetableEntry.id == entry.id)
    )
    return build_timetable_entry_out(result.scalar_one())


@router.put("/timetable/{entry_id}", response_model=TimetableEntryOut)
async def update_timetable_entry(
    entry_id: int,
    body: TimetableEntryUpdate,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    entry = await _get_owned_timetable_entry(entry_id, school_admin.school_id, db)

    updates = body.model_dump(exclude_unset=True)

    if updates.get("subject_id") is not None:
        await _get_owned_subject(updates["subject_id"], school_admin.school_id, db)

    teacher_id = updates.get("teacher_id", entry.teacher_id)
    if updates.get("teacher_id") is not None:
        await _get_owned_teacher_for_timetable(teacher_id, school_admin.school_id, db)

    day = updates.get("day_of_week", entry.day_of_week)
    period = updates.get("period_number", entry.period_number)
    academic_year = updates.get("academic_year", entry.academic_year)

    if (
        "day_of_week" in updates
        or "period_number" in updates
        or "teacher_id" in updates
        or "academic_year" in updates
    ):
        await _check_timetable_clashes(
            db, entry.class_id, teacher_id, day, period, academic_year,
            exclude_entry_id=entry.id,
        )

    if "start_time" in updates:
        updates["start_time"] = _str_to_time(updates["start_time"])
    if "end_time" in updates:
        updates["end_time"] = _str_to_time(updates["end_time"])

    for field, value in updates.items():
        setattr(entry, field, value)

    await db.commit()
    result = await db.execute(
        select(TimetableEntry)
        .options(
            selectinload(TimetableEntry.class_),
            selectinload(TimetableEntry.subject),
            selectinload(TimetableEntry.teacher),
        )
        .where(TimetableEntry.id == entry.id)
    )
    return build_timetable_entry_out(result.scalar_one())


@router.delete("/timetable/{entry_id}", status_code=204)
async def delete_timetable_entry(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    school_admin=Depends(get_active_school_admin),
):
    entry = await _get_owned_timetable_entry(entry_id, school_admin.school_id, db)
    await db.delete(entry)
    await db.commit()


async def _get_owned_timetable_entry(
    entry_id: int, school_id: int, db: AsyncSession
) -> TimetableEntry:
    result = await db.execute(
        select(TimetableEntry).where(TimetableEntry.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if not entry or entry.school_id != school_id:
        raise HTTPException(status_code=404, detail="Timetable entry not found")
    return entry


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
        status=admin.status,
        email_verified=admin.email_verified,
        rejected_reason=admin.rejected_reason,
        profile_pic_url=admin.profile_pic_url,
        school_id=admin.school_id,
        school_name=school.name,
        school_logo_url=school.logo_url,
        school_address=school.address,
        streak_min_recordings=school.streak_min_recordings,
        created_at=admin.created_at,

    )
