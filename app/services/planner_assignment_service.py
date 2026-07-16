from sqlalchemy.ext.asyncio import AsyncSession

from app.models.school_admin import SchoolAdmin

from sqlalchemy import select
from sqlalchemy import func
from app.models.teacher import Teacher
from app.core.security import hash_password

from app.models.class_ import Class
from app.models.subject import Subject

from app.schemas.quick_assignment import (
    QuickAssignmentIn,
    QuickAssignmentOut,
    ReplaceAssignmentIn,
    ReplaceAssignmentOut,
)


async def get_or_create_teacher(
    db: AsyncSession,
    school_admin: SchoolAdmin,
    body: QuickAssignmentIn,
) -> Teacher:

    teacher = await db.scalar(
        select(Teacher).where(
            Teacher.school_id == school_admin.school_id,
            Teacher.email == body.teacher_email,
        )
    )

    if teacher:
        return teacher

    teacher = Teacher(
        name=body.teacher_name,
        email=body.teacher_email,
        hashed_password=hash_password(body.temporary_password),
        school_id=school_admin.school_id,
    )

    db.add(teacher)
    await db.flush()
    return teacher

async def quick_assignment(
    db: AsyncSession,
    school_admin: SchoolAdmin,
    body: QuickAssignmentIn,
) -> QuickAssignmentOut:
    """
    Planner Assignment Workflow

    1. Find/Create Teacher
    2. Find/Create Class
    3. Find/Create Subject
    4. Validate Existing Assignment
    5. Replace Teacher (optional)
    6. Commit
    """

    teacher = await get_or_create_teacher(
        db=db,
        school_admin=school_admin,
        body=body,
    )
    
    
    class_ = await get_or_create_class(
        db=db,
        school_admin=school_admin,
        body=body,
    )


    subject = await get_or_create_subject(
        db=db,
        class_=class_,
        body=body,
    )
    
    if subject.teacher_id is None:
        subject.teacher_id = teacher.id

        await db.commit()

        await db.refresh(subject)

        return QuickAssignmentOut(
            status="created",
            message="Teacher assigned successfully.",

            teacher_id=teacher.id,
            class_id=class_.id,
            subject_id=subject.id,
        )
    
    if subject.teacher_id == teacher.id:
        return QuickAssignmentOut(
            status="already_assigned",
            message="Teacher is already assigned to this subject.",

            teacher_id=teacher.id,
            class_id=class_.id,
            subject_id=subject.id,
        )
 
    current_teacher = await db.get(
        Teacher,
        subject.teacher_id,
    )

    return QuickAssignmentOut(
        status="replace_required",
        message=f"{subject.name} is already assigned to {current_teacher.name}.",

        teacher_id=teacher.id,
        class_id=class_.id,
        subject_id=subject.id,

        current_teacher_name=current_teacher.name,
        current_teacher_email=current_teacher.email,
    )

async def replace_assignment(
    db: AsyncSession,
    school_admin: SchoolAdmin,
    body: ReplaceAssignmentIn,
) -> ReplaceAssignmentOut:
    subject = await db.scalar(
    select(Subject)
    .join(Class)
    .where(
        Subject.id == body.subject_id,
        Class.school_id == school_admin.school_id,
    )
    )

    if subject is None:
        return ReplaceAssignmentOut(
            success=False,
            message="Subject not found.",
        )
    
    teacher = await db.scalar(
        select(Teacher).where(
            Teacher.id == body.teacher_id,
            Teacher.school_id == school_admin.school_id,
        )
    )

    if teacher is None:
        return ReplaceAssignmentOut(
            success=False,
            message="Teacher not found.",
        )
    
    if subject.teacher_id == teacher.id:
        return ReplaceAssignmentOut(
            success=True,
            message="Teacher is already assigned.",
        )
    subject.teacher_id = teacher.id
    
    await db.commit()
    await db.refresh(subject)
    return ReplaceAssignmentOut(
        success=True,
        message="Teacher replaced successfully.",
    )   
    
async def get_or_create_class(
    db: AsyncSession,
    school_admin: SchoolAdmin,
    body: QuickAssignmentIn,
) -> Class:
    
    class_ = await db.scalar(
        select(Class).where(
            Class.school_id == school_admin.school_id,
            Class.name == body.class_name,
            Class.section == body.section,
        )
    )

    if class_:
        return class_
    
    class_ = Class(
        name=body.class_name,
        section=body.section,
        school_id=school_admin.school_id,
    )

    db.add(class_)

    await db.flush()

    return class_


async def get_or_create_subject(
    db: AsyncSession,
    class_: Class,
    body: QuickAssignmentIn,
) -> Subject:

    subject = await db.scalar(
        select(Subject).where(
            Subject.class_id == class_.id,
            func.lower(Subject.name) == body.subject_name.lower(),
        )
    )

    if subject:
        return subject

    subject = Subject(
        name=body.subject_name,
        class_id=class_.id,
    )

    db.add(subject)

    await db.flush()

    return subject