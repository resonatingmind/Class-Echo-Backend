from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class StudentSignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    mobile_number: str | None = None
    school_id: int
    class_id: int


class SchoolRegisterRequest(BaseModel):
    school_name: str
    school_address: str | None = None
    admin_name: str
    admin_email: EmailStr
    admin_password: str

class AcceptInviteRequest(BaseModel):
    token: str
    password: str

class TeacherRegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    school_id: int


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=8)
