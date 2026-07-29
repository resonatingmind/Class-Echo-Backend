from datetime import datetime
from pydantic import BaseModel, Field


class LearningPlanCreate(BaseModel):
    date: str | None = Field(None, max_length=50)
    topic_name: str = Field(..., min_length=1, max_length=255)
    sub_topic: str = Field(..., min_length=1, max_length=255)
    classwork_activities: str | None = None
    assessment_task: str | None = Field(None, max_length=255)
    teaching_aids: str | None = Field(None, max_length=255)
    learning_outcomes: str | None = None


class LearningPlanUpdate(BaseModel):
    date: str | None = Field(None, max_length=50)
    topic_name: str | None = Field(None, min_length=1, max_length=255)
    sub_topic: str | None = Field(None, min_length=1, max_length=255)
    classwork_activities: str | None = None
    assessment_task: str | None = Field(None, max_length=255)
    teaching_aids: str | None = Field(None, max_length=255)
    learning_outcomes: str | None = None


class LearningPlanOut(BaseModel):
    id: int
    chapter_id: int
    date: str | None = None
    topic_name: str
    sub_topic: str
    classwork_activities: str | None = None
    assessment_task: str | None = None
    teaching_aids: str | None = None
    learning_outcomes: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
