from datetime import datetime
from pydantic import BaseModel


class RecentLectureOut(BaseModel):
    recording_id: int
    lecture_title: str | None = None
    chapter_name: str | None = None
    uploaded_at: datetime
    overall_score: float | None = None
    status: str


class SubjectPerformanceOut(BaseModel):
    subject_id: int
    subject_name: str

    lectures: int

    average_score: float

    curriculum_coverage: float

    engagement_score: float

    accuracy_score: float


class MonthlyPerformanceOut(BaseModel):
    month: str

    lectures: int

    average_score: float

class TeacherAnalyticsOut(BaseModel):
    teacher_id: int
    teacher_name: str

    total_lectures: int
    completed_lectures: int
    pending_lectures: int
    failed_lectures: int

    # Core Scores
    overall_score: float
    engagement_score: float
    confidence_score: float
    curriculum_coverage: float
    accuracy_score: float

    # Speech Quality
    clarity_score: float
    fluency_score: float
    pace_score: float

    average_duration_seconds: float
    highest_score: float

    lowest_score: float

    longest_lecture_seconds: float

    shortest_lecture_seconds: float

    total_recording_hours: float

    recent_lectures: list[RecentLectureOut]
    subject_performance: list[SubjectPerformanceOut] = []

    monthly_performance: list[MonthlyPerformanceOut] = []