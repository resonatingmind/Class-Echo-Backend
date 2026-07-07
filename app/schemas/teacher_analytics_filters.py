from pydantic import BaseModel


class FilterOption(BaseModel):
    id: int
    name: str


class AnalyticsFiltersOut(BaseModel):
    classes: list[FilterOption]

    subjects: list[FilterOption]

    statuses: list[str]

    semesters: list[int]