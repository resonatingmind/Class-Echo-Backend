from pydantic import BaseModel


class TeacherFilterOption(BaseModel):
    id: int
    name: str


class ClassFiltersOut(BaseModel):
    """Filter options for the school admin classes list, derived from the
    school's actual data so every option yields at least one result."""

    classes: list[str]

    sections: list[str]

    teachers: list[TeacherFilterOption]

    subjects: list[str]
