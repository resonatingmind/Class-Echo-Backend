from datetime import datetime
from typing import Literal

from pydantic import BaseModel


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


class TrendsExclusionIn(BaseModel):
    excluded: bool


class TrendsExclusionOut(BaseModel):
    excluded_from_trends: bool
