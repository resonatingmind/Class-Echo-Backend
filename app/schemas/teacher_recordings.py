from datetime import datetime
from pydantic import BaseModel


class TeacherRecordingOut(BaseModel):
    id: int

    lecture_title: str | None = None
    chapter_name: str | None = None

    uploaded_at: datetime

    status: str

    overall_score: float | None = None

    subject_name: str

    cloudinary_url: str

    duration_seconds: float | None = None