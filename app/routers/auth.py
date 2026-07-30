from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.core.security import verify_password, hash_password, create_access_token, decode_token
from app.schemas.auth import LoginRequest, TokenResponse, StudentSignupRequest, SchoolRegisterRequest, TeacherRegisterRequest, AcceptInviteRequest
from app.models.admin import Admin
from app.models.school_admin import SchoolAdmin
from app.models.teacher import Teacher
from app.models.student import Student
from app.models.school import School
from app.models.class_ import Class

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


@router.post("/school/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def school_register(body: SchoolRegisterRequest, db: AsyncSession = Depends(get_db)):
    # Check email not already taken across all user tables
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
        email_verified=True,
    )
    db.add(school_admin)
    await db.commit()
    await db.refresh(school_admin)

    token_data = {"sub": str(school_admin.id), "role": "school_admin", "school_id": school.id}
    token = create_access_token(token_data)
    return TokenResponse(access_token=token, role="school_admin")


@router.post("/teacher/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def teacher_register(body: TeacherRegisterRequest, db: AsyncSession = Depends(get_db)):
    # Validate school exists
    school = await db.get(School, body.school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    # Check email not already taken across all user tables
    for _, Model in ROLE_MODELS:
        result = await db.execute(select(Model).where(Model.email == body.email))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already registered")

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

    token = create_access_token({"sub": str(teacher.id), "role": "teacher"})
    return TokenResponse(access_token=token, role="teacher")


@router.post("/accept-invite", response_model=TokenResponse)
async def accept_invite(body: AcceptInviteRequest, db: AsyncSession = Depends(get_db)):
    from jose import JWTError
    try:
        payload = decode_token(body.token)
        teacher_id = int(payload.get("sub"))
        role = payload.get("role")
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid or expired invite token")

    if role != "teacher":
        raise HTTPException(status_code=400, detail="Invalid token role")

    teacher = await db.get(Teacher, teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")

    if teacher.status != "invited":
        raise HTTPException(status_code=400, detail="Invite has already been accepted or is invalid")

    teacher.hashed_password = hash_password(body.password)
    teacher.status = "active"
    await db.commit()

    token = create_access_token({"sub": str(teacher.id), "role": "teacher"})
    return TokenResponse(access_token=token, role="teacher")
