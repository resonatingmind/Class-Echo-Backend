from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import Admin
from app.models.school_admin import SchoolAdmin
from app.models.student import Student
from app.models.teacher import Teacher

# Possible outcomes of classify_teacher_signup:
CREATE_NEW = "create_new"                          # email is free, caller may create a new Teacher row
EXISTING_INVITED_SAME_SCHOOL = "existing_invited_same_school"   # same school already has an invited-not-yet-accepted row
EXISTING_PENDING_SAME_SCHOOL = "existing_pending_same_school"    # same school already has a pending self-registration
CONFLICT_OTHER_SCHOOL = "conflict_other_school"     # this teacher email belongs to a different school
CONFLICT_TAKEN = "conflict_taken"                   # email is already active/suspended/rejected, or used by another role


async def classify_teacher_signup(
    db: AsyncSession, email: str, school_id: int
) -> tuple[str, Teacher | None]:
    """Classify what should happen when `email` is being invited-by-school-admin
    or self-registering for `school_id`, so the two onboarding entry points can
    merge into the same Teacher record instead of erroring or duplicating."""
    for model in (Admin, SchoolAdmin, Student):
        result = await db.execute(select(model).where(model.email == email))
        if result.scalar_one_or_none():
            return CONFLICT_TAKEN, None

    result = await db.execute(select(Teacher).where(Teacher.email == email))
    existing = result.scalar_one_or_none()

    if existing is None:
        return CREATE_NEW, None

    if existing.school_id != school_id:
        return CONFLICT_OTHER_SCHOOL, existing

    if existing.status == "invited":
        return EXISTING_INVITED_SAME_SCHOOL, existing

    if existing.status == "pending_approval":
        return EXISTING_PENDING_SAME_SCHOOL, existing

    return CONFLICT_TAKEN, existing
