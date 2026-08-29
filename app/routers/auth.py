from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.core.security import (
    verify_password,
    hash_password,
    create_access_token,
    hash_token,
    generate_token_pair,
)
from app.schemas.auth import (
    LoginRequest,
    TokenResponse,
    StudentSignupRequest,
    AcceptInviteRequest,
    TeacherRegisterRequest,
    SchoolRegisterRequest,
)
from app.models.admin import Admin
from app.models.school_admin import SchoolAdmin
from app.models.teacher import Teacher
from app.models.student import Student
from app.models.school import School
from app.models.class_ import Class
from app.models.auth_token import AuthToken
from app.services import teacher_onboarding_service as onboarding
from app.services.email_service import deliver_email
from app.config import settings

router = APIRouter()

ROLE_MODELS = [
    ("admin", Admin),
    ("school_admin", SchoolAdmin),
    ("teacher", Teacher),
    ("student", Student),
]


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, db: AsyncSession = Depends(get_db)):
    content_type = request.headers.get("content-type", "")
    email = None
    password = None

    if "application/x-www-form-urlencoded" in content_type:
        form_data = await request.form()
        email = form_data.get("username")
        password = form_data.get("password")
    else:
        try:
            json_data = await request.json()
            email = json_data.get("email") or json_data.get("username")
            password = json_data.get("password")
        except Exception:
            pass

    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Missing email/username or password",
        )

    for role, Model in ROLE_MODELS:
        result = await db.execute(select(Model).where(Model.email == email))
        user = result.scalar_one_or_none()
        if user and verify_password(password, user.hashed_password):
            token_data = {"sub": str(user.id), "role": role}
            if role == "school_admin":
                token_data["school_id"] = user.school_id
            token = create_access_token(token_data)
            return TokenResponse(access_token=token, role=role)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
    )


@router.post("/student/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def student_signup(body: StudentSignupRequest, db: AsyncSession = Depends(get_db)):
    # Validate school and class exist
    school = await db.get(School, body.school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    class_ = await db.get(Class, body.class_id)
    if not class_ or class_.school_id != body.school_id:
        raise HTTPException(status_code=404, detail="Class not found in this school")

    # Check email not already taken across all user tables
    for _, Model in ROLE_MODELS:
        result = await db.execute(select(Model).where(Model.email == body.email))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already registered")

    student = Student(
        name=body.name,
        email=body.email,
        hashed_password=hash_password(body.password),
        mobile_number=body.mobile_number,
        school_id=body.school_id,
        class_id=body.class_id,
    )
    db.add(student)
    await db.commit()
    await db.refresh(student)

    token = create_access_token({"sub": str(student.id), "role": "student"})
    return TokenResponse(access_token=token, role="student")


ONBOARDING_MODELS = {"school_admin": SchoolAdmin, "teacher": Teacher}


@router.post("/accept-invite", response_model=TokenResponse)
async def accept_invite(body: AcceptInviteRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hash_token(body.token)
    result = await db.execute(
        select(AuthToken).where(
            AuthToken.token_hash == token_hash,
            AuthToken.purpose == "invite",
        )
    )
    invite = result.scalar_one_or_none()
    if not invite or invite.used_at or invite.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="This invite link is invalid or has expired")

    Model = ONBOARDING_MODELS.get(invite.user_type)
    if Model is None:
        raise HTTPException(status_code=400, detail="This invite link is invalid or has expired")

    user = await db.get(Model, invite.user_id)
    if not user:
        raise HTTPException(status_code=400, detail="This invite link is invalid or has expired")

    user.hashed_password = hash_password(body.password)
    user.status = "active"
    # Clicking an emailed invite link is itself proof of email ownership.
    user.email_verified = True
    invite.used_at = datetime.utcnow()
    await db.commit()

    token = create_access_token({"sub": str(user.id), "role": invite.user_type})
    return TokenResponse(access_token=token, role=invite.user_type)


@router.post(
    "/teacher/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def teacher_register(body: TeacherRegisterRequest, db: AsyncSession = Depends(get_db)):
    school = await db.get(School, body.school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    action, existing = await onboarding.classify_teacher_signup(db, body.email, body.school_id)

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
            detail="You already have a registration request pending approval for this school",
        )

    if action == onboarding.EXISTING_INVITED_SAME_SCHOOL:
        # Complete the school admin's invite using the password chosen here.
        teacher = existing
        teacher.name = body.name
        teacher.hashed_password = hash_password(body.password)
        teacher.status = "active"
    else:
        teacher = Teacher(
            name=body.name,
            email=body.email,
            hashed_password=hash_password(body.password),
            school_id=body.school_id,
            status="pending_approval",
        )
        db.add(teacher)

    await db.commit()
    await db.refresh(teacher)

    if teacher.status == "pending_approval":
        raw_token, token_hash = generate_token_pair()
        db.add(
            AuthToken(
                user_type="teacher",
                user_id=teacher.id,
                token_hash=token_hash,
                purpose="email_verification",
                expires_at=datetime.utcnow() + timedelta(days=3),
            )
        )
        await db.commit()
        verify_link = f"{settings.FRONTEND_URL.rstrip('/')}/verify-email?token={raw_token}"
        deliver_email(
            teacher.email,
            "Verify your email — Class Echo",
            f"<p>Hi {teacher.name},</p><p>Verify your email by clicking "
            f"<a href='{verify_link}'>this link</a>. This is optional and won't "
            f"block your access.</p>",
            purpose="verification",
            school_id=teacher.school_id,
            related_type="teacher",
            related_id=teacher.id,
        )

    token = create_access_token({"sub": str(teacher.id), "role": "teacher"})
    return TokenResponse(access_token=token, role="teacher")


@router.post(
    "/school/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def school_register(body: SchoolRegisterRequest, db: AsyncSession = Depends(get_db)):
    for _, Model in ROLE_MODELS:
        result = await db.execute(select(Model).where(Model.email == body.admin_email))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already registered")

    school = School(name=body.school_name, address=body.school_address)
    db.add(school)
    await db.flush()  # get school.id

    school_admin = SchoolAdmin(
        name=body.admin_name,
        email=body.admin_email,
        hashed_password=hash_password(body.admin_password),
        school_id=school.id,
        status="pending_approval",
    )
    db.add(school_admin)
    await db.commit()
    await db.refresh(school_admin)

    raw_token, token_hash = generate_token_pair()
    db.add(
        AuthToken(
            user_type="school_admin",
            user_id=school_admin.id,
            token_hash=token_hash,
            purpose="email_verification",
            expires_at=datetime.utcnow() + timedelta(days=3),
        )
    )
    await db.commit()
    verify_link = f"{settings.FRONTEND_URL.rstrip('/')}/verify-email?token={raw_token}"
    deliver_email(
        school_admin.email,
        "Verify your email — Class Echo",
        f"<p>Hi {school_admin.name},</p><p>Verify your email by clicking "
        f"<a href='{verify_link}'>this link</a>. This is optional and won't "
        f"block your access.</p>",
        purpose="verification",
        school_id=school.id,
        related_type="school_admin",
        related_id=school_admin.id,
    )

    token = create_access_token({"sub": str(school_admin.id), "role": "school_admin"})
    return TokenResponse(access_token=token, role="school_admin")


@router.get("/verify-email")
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    token_hash = hash_token(token)
    result = await db.execute(
        select(AuthToken).where(
            AuthToken.token_hash == token_hash,
            AuthToken.purpose == "email_verification",
        )
    )
    record = result.scalar_one_or_none()
    if not record or record.used_at or record.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="This verification link is invalid or has expired")

    Model = ONBOARDING_MODELS.get(record.user_type)
    user = await db.get(Model, record.user_id) if Model else None
    if not user:
        raise HTTPException(status_code=400, detail="This verification link is invalid or has expired")

    user.email_verified = True
    record.used_at = datetime.utcnow()
    await db.commit()
    return {"email_verified": True}
