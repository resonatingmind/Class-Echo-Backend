from pydantic import BaseModel
from datetime import datetime

class SchoolDashboardOverviewOut(BaseModel):
    total_teachers: int
    total_students: int
    total_classes: int
    total_subjects: int
    total_recordings: int
    pending_reports: int


class SchoolAnalyticsOut(BaseModel):
    overall_score: float

    engagement_score: float

    confidence_score: float

    clarity_score: float

    fluency_score: float

    pace_score: float

    curriculum_coverage: float

    accuracy_score: float
    

class TeacherRankingItem(BaseModel):
    teacher_id: int
    teacher_name: str
    total_lectures: int
    overall_score: float


class TeacherRankingOut(BaseModel):
    teachers: list[TeacherRankingItem]
    
    

class RecentActivityItem(BaseModel):
    recording_id: int

    teacher_name: str

    lecture_title: str | None = None

    chapter_name: str | None = None

    uploaded_at: datetime

    status: str


class RecentActivityOut(BaseModel):
    activities: list[RecentActivityItem]