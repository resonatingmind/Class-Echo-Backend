from pydantic import BaseModel, EmailStr, model_validator
from app.services.s3_service import sign_url
# Rest of file imports...
from datetime import datetime

class SchoolCreate(BaseModel):
    name: str
    address: str | None = None
    logo_url: str | None = None
    # School admin is created invited (no password) and accepts via emailed link.
    admin_name: str
    admin_email: EmailStr


class SchoolUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    logo_url: str | None = None


class SchoolAdminOut(BaseModel):
    id: int
    name: str
    email: str
    status: str = "active"
    email_verified: bool = False

    class Config:
        from_attributes = True


class SchoolOut(BaseModel):
    id: int
    name: str
    address: str | None
    logo_url: str | None
    created_at: datetime
    admin: SchoolAdminOut | None = None

    class Config:
        from_attributes = True

    @model_validator(mode="after")
    def sign_s3_url(self) -> "SchoolOut":
        if self.logo_url:
            self.logo_url = sign_url(self.logo_url)
        return self
