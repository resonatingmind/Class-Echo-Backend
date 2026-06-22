from datetime import datetime
from pydantic import BaseModel, Field


class ChapterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None


class ChapterUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None


class ChapterOut(BaseModel):
    id: int
    subject_id: int
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChapterDocumentOut(BaseModel):
    id: int
    chapter_id: int
    file_name: str
    storage_url: str
    file_type: str
    processing_status: str
    uploaded_at: datetime

    class Config:
        from_attributes = True

