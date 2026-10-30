from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class GrowthPoint(BaseModel):
    recording_id: int
    recorded_at: datetime
    value: float


class AreaTrendOut(BaseModel):
    # A rubric dimension, or "overall" for the total score.
    area: str
    # 0-100: the area's score as a share of its maximum, averaged.
    recent_avg: float | None = None
    previous_avg: float | None = None
    # recent_avg - previous_avg in points; None until both windows have enough lectures.
    change: float | None = None
    direction: Literal["improving", "steady", "declining"] | None = None
    # Oldest first, the most recent lectures only.
    series: list[GrowthPoint] = []


class FocusProgressOut(BaseModel):
    area: str | None = None
    try_this: str
    recording_id: int
    # Of the last `recent` lectures, how many scored above the earlier average in this area.
    recent: int = 0
    improved: int = 0


class StreakOut(BaseModel):
    # Weeks in a row with at least `threshold` recordings; holidays skipped.
    weeks: int = 0
    best: int = 0
    threshold: int = 2
    # Recordings so far this week (the week still under way).
    this_week: int = 0


class MilestoneOut(BaseModel):
    id: int
    # first_recording, ten_lectures, streak_4_weeks, focus_achieved:<area>, best_engagement,
    # chapter_covered:<chapter id>, five_self_ratings, self_match_3
    key: str
    achieved_at: datetime
    meta: dict = {}
    shared: bool = False


class MilestoneShareIn(BaseModel):
    shared: bool


TeacherStatus = Literal["improving", "steady", "needs_support"]


class GrowthOut(BaseModel):
    """My Growth: the teacher compared only with their own past."""

    rubric_version: str
    window: int
    # Lectures in the trends: this rubric, not excluded by the teacher.
    lecture_count: int
    # Scored under an earlier rubric, so not comparable and left out.
    older_rubric_count: int
    excluded_count: int
    overall: AreaTrendOut
    areas: list[AreaTrendOut]
    focus: FocusProgressOut | None = None
    streak: StreakOut | None = None
    # Newest first. On the admin view, only the ones the teacher shared.
    milestones: list[MilestoneOut] = []


class SharedClipForAdminOut(BaseModel):
    """A moment a teacher shared, with just enough of the lecture to play it."""

    id: int
    start: float
    title: str
    note: str | None = None
    recording_id: int
    lecture_title: str | None = None
    recorded_at: datetime
    audio_url: str
    # Only the transcript lines inside the clip's window.
    segments: list[dict] = []


class AdminTeacherGrowthOut(BaseModel):
    teacher_id: int
    name: str
    # None until there are enough lectures to compare.
    status: TeacherStatus | None = None
    growth: GrowthOut
    shared_clips: list[SharedClipForAdminOut] = []


class TeacherGrowthSummaryOut(BaseModel):
    teacher_id: int
    name: str
    status: TeacherStatus | None = None
    lecture_count: int
    overall_change: float | None = None
    focus_area: str | None = None
    streak_weeks: int = 0


class AreaPatternOut(BaseModel):
    area: str
    # Teachers whose weakest area this is, and their share of the teachers counted.
    teachers: int
    share: float


class SchoolGrowthOut(BaseModel):
    """Growth across the school's teachers for the school admin. No ranking."""

    teachers: list[TeacherGrowthSummaryOut]
    # Teachers with enough lectures to have a weakest area.
    teachers_counted: int
    weakest_areas: list[AreaPatternOut]
    status_counts: dict[str, int]


class StreakSettingIn(BaseModel):
    streak_min_recordings: int = Field(ge=1, le=10)


class TrendsExclusionIn(BaseModel):
    excluded: bool


class TrendsExclusionOut(BaseModel):
    excluded_from_trends: bool
