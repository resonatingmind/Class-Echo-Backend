from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from app.schemas.learning_plan import LearningPlanOut


class LastLessonOut(BaseModel):
    recording_id: int
    title: str | None = None
    recorded_at: datetime
    # True when another teacher taught it (the teacher on the timetable is a substitute).
    by_other_teacher: bool = False
    covered: list[str] = []
    unfinished: list[str] = []
    recall_questions: list[str] = []


class BriefFocusOut(BaseModel):
    recording_id: int
    area: str | None = None
    observation: str
    try_this: str


class ClassBriefOut(BaseModel):
    """What the teacher sees on the Next class card before a timetabled period."""

    timetable_entry_id: int
    subject_id: int
    muted: bool = False
    # None when the class has no completed recording yet.
    last_lesson: LastLessonOut | None = None
    todays_plan: LearningPlanOut | None = None
    # The primary try-this from the teacher's own latest report, any class.
    focus: BriefFocusOut | None = None


class ClassBriefMuteIn(BaseModel):
    muted: bool


class ClassBriefOpenIn(BaseModel):
    # The teacher's local date of the class.
    on: date
    source: Literal["dashboard", "push"] = "dashboard"


class DeviceTokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=255)
    platform: Literal["ios", "android"]
    timezone: str = Field(min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Unknown timezone")
        return value


class DeviceTokenRemoveIn(BaseModel):
    token: str = Field(min_length=10, max_length=255)
