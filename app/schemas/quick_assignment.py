from pydantic import BaseModel, EmailStr


class QuickAssignmentIn(BaseModel):
    teacher_name: str
    teacher_email: EmailStr
    temporary_password: str

    class_name: str
    section: str

    subject_name: str


class QuickAssignmentOut(BaseModel):
    status: str

    message: str

    teacher_id: int

    class_id: int

    subject_id: int

    current_teacher_name: str | None = None

    current_teacher_email: str | None = None

class ReplaceAssignmentIn(BaseModel):
    subject_id: int
    teacher_id: int


class ReplaceAssignmentOut(BaseModel):
    success: bool
    message: str