from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, model_validator
from app.services.s3_service import sign_url


class TeacherCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    profile_image_url: str | None = None


class TeacherOut(BaseModel):
    id: int
    name: str
    email: str
    profile_image_url: str | None
    school_id: int | None
    school_name: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def sign_s3_url(self) -> "TeacherOut":
        if self.profile_image_url:
            self.profile_image_url = sign_url(self.profile_image_url)
        return self


class TeacherUpdate(BaseModel):
    name: str | None = None


class TeacherSubjectSummary(BaseModel):
    id: int
    name: str
    class_name: str
    chapter_count: int
    recording_count: int

    class Config:
        from_attributes = True


class TeacherStatistics(BaseModel):
    total_classes: int
    total_subjects: int
    total_chapters: int
    total_recordings: int


class TeacherProfileOut(BaseModel):
    id: int
    name: str
    email: str

    assigned_at: datetime | None = None

    classes: list[str]

    subjects: list[TeacherSubjectSummary]

    statistics: TeacherStatistics

    class Config:
        from_attributes = True
