from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.core.deps import get_admin
from app.core.security import generate_token_pair, verify_password
from app.config import settings
from app.models.auth_token import AuthToken
from app.tasks.email_tasks import send_email_task
from app.models.school import School
from app.models.school_admin import SchoolAdmin
from app.models.class_ import Class
from app.models.subject import Subject
from app.models.student import Student
from app.models.recording import Recording
from app.models.llm_report import LLMReport
from app.schemas.school import (
    SchoolCreate,
    SchoolUpdate,
    SchoolOut,
    SchoolRejectRequest,
    SchoolDeleteRequest,
)
from app.schemas.admin import AdminProfileOut
from app.schemas.class_ import ClassOut
from app.schemas.subject import SubjectOut
from app.schemas.student import StudentOut
from app.schemas.recording import RecordingWithReport

router = APIRouter()


@router.get("/me", response_model=AdminProfileOut)
async def get_my_profile(admin=Depends(get_admin)):
    return admin


@router.get("/schools", response_model=list[SchoolOut])
async def list_schools(
    status: str | None = Query(None, description="Filter by invited/pending_approval/active/suspended/rejected"),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    query = select(School).options(selectinload(School.admin)).order_by(School.created_at.desc())
    if status is not None:
        query = query.join(SchoolAdmin, School.id == SchoolAdmin.school_id).where(
            SchoolAdmin.status == status
        )
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/schools", response_model=SchoolOut, status_code=201)
async def create_school(
    body: SchoolCreate,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    # Check admin email not already taken
    from app.models.admin import Admin
    from app.models.teacher import Teacher
    from app.models.student import Student
    for Model in (Admin, SchoolAdmin, Teacher, Student):
        r = await db.execute(select(Model).where(Model.email == body.admin_email))
        if r.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Admin email already registered")

    school = School(name=body.name, address=body.address, logo_url=body.logo_url)
    db.add(school)
    await db.flush()  # get school.id

    school_admin = SchoolAdmin(
        name=body.admin_name,
        email=body.admin_email,
        hashed_password=None,
        school_id=school.id,
        status="invited",
        invited_by_admin_id=admin.id,
    )
    db.add(school_admin)
    await db.flush()  # get school_admin.id

    raw_token, token_hash = generate_token_pair()
    db.add(
        AuthToken(
            user_type="school_admin",
            user_id=school_admin.id,
            token_hash=token_hash,
            purpose="invite",
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
    )
    await db.commit()
    await db.refresh(school)

    invite_link = f"{settings.FRONTEND_URL}/accept-invite?token={raw_token}"
    send_email_task.delay(
        school_admin.email,
        "You've been invited to Class Echo",
        f"<p>Hi {school_admin.name},</p><p>You've been set up as the admin for "
        f"{school.name} on Class Echo. Set your password to get started: "
        f"<a href='{invite_link}'>{invite_link}</a></p>",
    )

    result = await db.execute(
        select(School).options(selectinload(School.admin)).where(School.id == school.id)
    )
    return result.scalar_one()


@router.get("/schools/{school_id}", response_model=SchoolOut)
async def get_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    result = await db.execute(
        select(School).options(selectinload(School.admin)).where(School.id == school_id)
    )
    school = result.scalar_one_or_none()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    return school


@router.put("/schools/{school_id}", response_model=SchoolOut)
async def update_school(
    school_id: int,
    body: SchoolUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    result = await db.execute(select(School).where(School.id == school_id))
    school = result.scalar_one_or_none()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(school, field, value)
    await db.commit()
    await db.refresh(school)

    result = await db.execute(
        select(School).options(selectinload(School.admin)).where(School.id == school.id)
    )
    return result.scalar_one()


@router.delete("/schools/{school_id}", status_code=204)
async def delete_school(
    school_id: int,
    body: SchoolDeleteRequest,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    if not verify_password(body.password, admin.hashed_password):
        raise HTTPException(status_code=403, detail="Incorrect password")

    result = await db.execute(select(School).where(School.id == school_id))
    school = result.scalar_one_or_none()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    class_ids = select(Class.id).where(Class.school_id == school_id)
    subject_ids = select(Subject.id).where(Subject.class_id.in_(class_ids))
    recording_ids = select(Recording.id).where(Recording.subject_id.in_(subject_ids))

    await db.execute(
        delete(LLMReport).where(LLMReport.recording_id.in_(recording_ids))
    )
    await db.execute(
        delete(Recording).where(Recording.id.in_(recording_ids))
    )
    await db.execute(
        delete(Subject).where(Subject.id.in_(subject_ids))
    )
    await db.execute(
        delete(Student).where(Student.school_id == school_id)
    )
    await db.execute(
        delete(Class).where(Class.school_id == school_id)
    )
    await db.execute(
        delete(SchoolAdmin).where(SchoolAdmin.school_id == school_id)
    )
    await db.delete(school)
    await db.commit()


@router.post("/schools/{school_id}/approve", response_model=SchoolOut)
async def approve_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    school_admin = await _get_school_admin_for_school(school_id, db)
    if school_admin.status != "pending_approval":
        raise HTTPException(status_code=400, detail="Only pending schools can be approved")

    school_admin.status = "active"
    school_admin.approved_at = datetime.utcnow()
    school_admin.approved_by_admin_id = admin.id
    await db.commit()
    return await _get_school_with_admin(school_id, db)


@router.post("/schools/{school_id}/reject", response_model=SchoolOut)
async def reject_school(
    school_id: int,
    body: SchoolRejectRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    school_admin = await _get_school_admin_for_school(school_id, db)
    if school_admin.status != "pending_approval":
        raise HTTPException(status_code=400, detail="Only pending schools can be rejected")

    school_admin.status = "rejected"
    school_admin.rejected_reason = body.reason
    await db.commit()
    return await _get_school_with_admin(school_id, db)


@router.post("/schools/{school_id}/suspend", response_model=SchoolOut)
async def suspend_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    school_admin = await _get_school_admin_for_school(school_id, db)
    if school_admin.status != "active":
        raise HTTPException(status_code=400, detail="Only active schools can be suspended")

    school_admin.status = "suspended"
    await db.commit()
    return await _get_school_with_admin(school_id, db)


@router.post("/schools/{school_id}/reactivate", response_model=SchoolOut)
async def reactivate_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    school_admin = await _get_school_admin_for_school(school_id, db)
    if school_admin.status != "suspended":
        raise HTTPException(status_code=400, detail="Only suspended schools can be reactivated")

    school_admin.status = "active"
    await db.commit()
    return await _get_school_with_admin(school_id, db)


async def _get_school_admin_for_school(school_id: int, db: AsyncSession) -> SchoolAdmin:
    result = await db.execute(select(SchoolAdmin).where(SchoolAdmin.school_id == school_id))
    school_admin = result.scalar_one_or_none()
    if not school_admin:
        raise HTTPException(status_code=404, detail="School not found")
    return school_admin


async def _get_school_with_admin(school_id: int, db: AsyncSession) -> School:
    result = await db.execute(
        select(School).options(selectinload(School.admin)).where(School.id == school_id)
    )
    return result.scalar_one()


@router.get("/schools/{school_id}/classes", response_model=list[ClassOut])
async def list_school_classes(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    result = await db.execute(
        select(Class).where(Class.school_id == school_id).order_by(
    Class.name,
    Class.section,
)
    )
    return result.scalars().all()


@router.get("/schools/{school_id}/classes/{class_id}", response_model=ClassOut)
async def get_school_class(
    school_id: int,
    class_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    class_ = await _get_school_class(class_id, school_id, db)
    return class_


@router.get("/schools/{school_id}/classes/{class_id}/subjects", response_model=list[SubjectOut])
async def list_school_class_subjects(
    school_id: int,
    class_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    await _get_school_class(class_id, school_id, db)
    result = await db.execute(
        select(Subject)
        .options(selectinload(Subject.teacher))
        .where(Subject.class_id == class_id)
        .order_by(Subject.name)
    )
    return result.scalars().all()


@router.get("/schools/{school_id}/subjects/{subject_id}/students", response_model=list[StudentOut])
async def list_school_subject_students(
    school_id: int,
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    subject = await _get_school_subject(subject_id, school_id, db)
    result = await db.execute(
        select(Student)
        .where(Student.class_id == subject.class_id)
        .order_by(Student.name)
    )
    return result.scalars().all()


@router.get("/schools/{school_id}/subjects/{subject_id}/recordings", response_model=list[RecordingWithReport])
async def list_school_subject_recordings(
    school_id: int,
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    await _get_school_subject(subject_id, school_id, db)
    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.report).selectinload(LLMReport.recording),
            selectinload(Recording.learning_plan),
        )
        .where(Recording.subject_id == subject_id)
        .order_by(Recording.uploaded_at.desc())
    )
    return result.scalars().all()


@router.delete("/recordings/{recording_id}", status_code=204)
async def delete_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    await db.execute(delete(LLMReport).where(LLMReport.recording_id == recording_id))
    await db.delete(recording)
    await db.commit()


async def _get_school_class(class_id: int, school_id: int, db: AsyncSession) -> Class:
    result = await db.execute(
        select(Class).where(Class.id == class_id, Class.school_id == school_id)
    )
    class_ = result.scalar_one_or_none()
    if not class_:
        raise HTTPException(status_code=404, detail="Class not found")
    return class_


async def _get_school_subject(subject_id: int, school_id: int, db: AsyncSession) -> Subject:
    result = await db.execute(
        select(Subject)
        .join(Class, Subject.class_id == Class.id)
        .where(Subject.id == subject_id, Class.school_id == school_id)
    )
    subject = result.scalar_one_or_none()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject
