from datetime import datetime
from pydantic import BaseModel, model_validator
from app.models.recording import RecordingStatus
from app.services.s3_service import sign_url


class RecordingOut(BaseModel):
    id: int
    subject_id: int
    teacher_id: int
    chapter_name: str | None
    description: str | None
    cloudinary_url: str
    duration_seconds: float | None
    status: RecordingStatus
    uploaded_at: datetime
    processed_at: datetime | None

    class Config:
        from_attributes = True

    @model_validator(mode="after")
    def sign_s3_url(self) -> "RecordingOut":
        if self.cloudinary_url:
            self.cloudinary_url = sign_url(self.cloudinary_url)
        return self


class LLMReportOut(BaseModel):
    id: int
    recording_id: int
    overall_score: float | None
    teaching_quality_notes: str | None
    score_breakdown: dict | None
    quantitative_metrics: dict | None
    created_at: datetime

    class Config:
        from_attributes = True


class RecordingWithReport(RecordingOut):
    report: LLMReportOut | None = None
