from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.core.security import decode_token
from app.models.admin import Admin
from app.models.school_admin import SchoolAdmin
from app.models.teacher import Teacher
from app.models.student import Student

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    try:
        payload = decode_token(token)
        user_id: int = int(payload.get("sub"))
        role: str = payload.get("role")
    except (JWTError, TypeError, ValueError):
        raise CREDENTIALS_EXCEPTION

    model_map = {
        "admin": Admin,
        "school_admin": SchoolAdmin,
        "teacher": Teacher,
        "student": Student,
    }
    model = model_map.get(role)
    if not model:
        raise CREDENTIALS_EXCEPTION

    result = await db.execute(select(model).where(model.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise CREDENTIALS_EXCEPTION

    return user, role


def require_role(*roles: str):
    async def dependency(current=Depends(get_current_user)):
        user, role = current
        if role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return dependency


async def get_admin(current=Depends(get_current_user)):
    user, role = current
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def get_school_admin(current=Depends(get_current_user)):
    user, role = current
    if role != "school_admin":
        raise HTTPException(status_code=403, detail="School admin access required")
    return user


async def get_teacher(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    try:
        payload = decode_token(token)
        user_id: int = int(payload.get("sub"))
        role: str = payload.get("role")
    except (JWTError, TypeError, ValueError):
        raise CREDENTIALS_EXCEPTION
    if role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher access required")
    result = await db.execute(
        select(Teacher).options(selectinload(Teacher.school)).where(Teacher.id == user_id)
    )
    teacher = result.scalar_one_or_none()
    if not teacher:
        raise CREDENTIALS_EXCEPTION
    return teacher


async def get_student(current=Depends(get_current_user)):
    user, role = current
    if role != "student":
        raise HTTPException(status_code=403, detail="Student access required")
    return user


TEACHER_STATUS_DETAIL = {
    "invited": "teacher_invited",
    "pending_approval": "teacher_pending_approval",
    "rejected": "teacher_rejected",
    "suspended": "teacher_suspended",
}


async def get_active_teacher(teacher=Depends(get_teacher)):
    """Gate for teacher business endpoints. `/teacher/me` uses get_teacher
    directly so a pending/suspended teacher can still poll their own status."""
    if teacher.status != "active":
        detail = TEACHER_STATUS_DETAIL.get(teacher.status, "teacher_inactive")
        raise HTTPException(status_code=403, detail=detail)
    return teacher


SCHOOL_ADMIN_STATUS_DETAIL = {
    "invited": "school_admin_invited",
    "pending_approval": "school_admin_pending_approval",
    "rejected": "school_admin_rejected",
    "suspended": "school_admin_suspended",
}


async def get_active_school_admin(school_admin=Depends(get_school_admin)):
    """Gate for school-admin business endpoints. `/school/me` uses
    get_school_admin directly so a pending school can still poll its status."""
    if school_admin.status != "active":
        detail = SCHOOL_ADMIN_STATUS_DETAIL.get(school_admin.status, "school_admin_inactive")
        raise HTTPException(status_code=403, detail=detail)
    return school_admin
