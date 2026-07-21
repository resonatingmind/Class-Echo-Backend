from datetime import datetime
from pydantic import BaseModel, field_validator

DAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
   
    "Saturday",
]

DEFAULT_ACADEMIC_YEAR = "2026-27"


def _validate_time(value: str) -> str:
    value = value.strip()
    parts = value.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError('Time must be in "HH:MM" 24-hour format')
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError('Time must be in "HH:MM" 24-hour format')
    return f"{hour:02d}:{minute:02d}"


def _validate_day(value: str) -> str:
    normalized = value.strip().capitalize()
    if normalized not in DAY_NAMES:
        raise ValueError(f"day_of_week must be one of {', '.join(DAY_NAMES)}")
    return normalized


class TimetableEntryCreate(BaseModel):
    class_id: int
    subject_id: int
    teacher_id: int
    day_of_week: str
    period_number: int
    start_time: str
    end_time: str
    room: str | None = None
    academic_year: str = DEFAULT_ACADEMIC_YEAR

    @field_validator("day_of_week")
    @classmethod
    def check_day(cls, v: str) -> str:
        return _validate_day(v)

    @field_validator("period_number")
    @classmethod
    def check_period(cls, v: int) -> int:
        if v < 1:
            raise ValueError("period_number must be 1 or greater")
        return v

    @field_validator("start_time", "end_time")
    @classmethod
    def check_time(cls, v: str) -> str:
        return _validate_time(v)


class TimetableEntryUpdate(BaseModel):
    subject_id: int | None = None
    teacher_id: int | None = None
    day_of_week: str | None = None
    period_number: int | None = None
    start_time: str | None = None
    end_time: str | None = None
    room: str | None = None
    academic_year: str | None = None

    @field_validator("day_of_week")
    @classmethod
    def check_day(cls, v: str | None) -> str | None:
        return _validate_day(v) if v is not None else v

    @field_validator("period_number")
    @classmethod
    def check_period(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("period_number must be 1 or greater")
        return v

    @field_validator("start_time", "end_time")
    @classmethod
    def check_time(cls, v: str | None) -> str | None:
        return _validate_time(v) if v is not None else v


class TeacherBrief(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True


class SubjectBrief(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True


class ClassBrief(BaseModel):
    id: int
    name: str
    section: str

    class Config:
        from_attributes = True


class TimetableEntryOut(BaseModel):
    id: int
    school_id: int
    class_id: int
    subject_id: int
    teacher_id: int
    day_of_week: str
    period_number: int
    start_time: str
    end_time: str
    room: str | None
    academic_year: str
    class_: ClassBrief | None = None
    subject: SubjectBrief | None = None
    teacher: TeacherBrief | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TimetableImportResult(BaseModel):
    created: int
    updated: int
    skipped: int
    errors: list[str] = []