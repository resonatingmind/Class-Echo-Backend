from datetime import datetime
from pydantic import BaseModel


class SubjectCreate(BaseModel):
    name: str
    profile_image_url: str | None = None


class SubjectUpdate(BaseModel):
    name: str | None = None
    profile_image_url: str | None = None


class AssignTeacherRequest(BaseModel):
    teacher_id: int


class TeacherBrief(BaseModel):
    id: int
    name: str
    email: str

    class Config:
        from_attributes = True


class SubjectOut(BaseModel):
    id: int
    name: str
    profile_image_url: str | None
    class_id: int
    teacher_id: int | None
    teacher: TeacherBrief | None
    teacher_assigned_at: datetime | None = None
    created_at: datetime

    class Config:
        from_attributes = True


# class ClassBrief(BaseModel):
#     id: int
#     name: str
#     class Config:
#         from_attributes = True

class ClassBrief(BaseModel):
    id: int
    name: str
    section: str
    profile_image_url: str | None = None

    class Config:
        from_attributes = True


# class SubjectWithClassOut(SubjectOut):
#     """SubjectOut extended with class details — used for teacher-facing routes."""
#     class_: ClassBrief | None = None

#     class Config:
#         from_attributes = True

class SubjectWithClassOut(SubjectOut):
    class_: ClassBrief | None = None

    academic_year: str = "2026-27"
    status: str = "Active"
    class Config:
        from_attributes = True
