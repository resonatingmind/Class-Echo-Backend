from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator
from app.services.s3_service import sign_url


class SchoolAdminProfileOut(BaseModel):
    id: int
    name: str
    email: str
    profile_pic_url: str | None
    school_id: int
    school_name: str
    school_logo_url: str | None
    school_address: str | None
    created_at: datetime
    status: str = "active"
    email_verified: bool = False
    rejected_reason: str | None = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def sign_s3_urls(self) -> "SchoolAdminProfileOut":
        if self.profile_pic_url:
            self.profile_pic_url = sign_url(self.profile_pic_url)
        if self.school_logo_url:
            self.school_logo_url = sign_url(self.school_logo_url)
        return self


class SchoolAdminUpdate(BaseModel):
    name: str | None = None
    school_name: str | None = None
    school_address: str | None = None
