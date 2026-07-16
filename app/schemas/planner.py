from datetime import datetime
from pydantic import BaseModel
from typing import Optional

class PlannerItemOut(BaseModel):
    subject_id: int

    class_id: int
    class_name: str
    section: str
    teacher_id: int | None
    teacher_name: str | None

    subject_name: str

    academic_year: str

    chapter_count: int

    recording_count: int

    created_at: datetime
    subject_profile_image_url: str | None
    teacher_profile_image_url: str | None

    class Config:
        from_attributes = True
        


class PlannerChapterOut(BaseModel):
    id: int
    name: str

    document_count: int

    recording_count: int

    subject_id: int
    teacher_id: int | None
    average_score: Optional[float]

    status: str

    class Config:
        from_attributes = True


class PlannerStatisticsOut(BaseModel):
    total_chapters: int
    total_documents: int
    total_recordings: int
    average_score: float | None

    class Config:
        from_attributes = True

class PlannerSubjectDetailOut(BaseModel):
    subject_id: int

    subject_name: str

    class_name: str

    teacher_name: Optional[str]

    academic_year: str
    statistics: PlannerStatisticsOut

    chapters: list[PlannerChapterOut]

    class Config:
        from_attributes = True