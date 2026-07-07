from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, model_validator
from app.services.s3_service import sign_url


class StudentOut(BaseModel):
    id: int
    name: str
    email: str
    mobile_number: str | None
    school_id: int
    class_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class StudentProfileOut(BaseModel):
    """Extended profile that includes resolved school and class names."""
    id: int
    name: str
    email: str
    profile_image_url: str | None
    mobile_number: str | None
    school_id: int
    school_name: str
    class_id: int
    class_name: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def sign_s3_url(self) -> "StudentProfileOut":
        if self.profile_image_url:
            self.profile_image_url = sign_url(self.profile_image_url)
        return self


class StudentWithClassOut(BaseModel):
    """Student with class name — used by school admin roster."""
    id: int
    name: str
    email: str
    profile_image_url: str | None
    mobile_number: str | None
    class_id: int
    class_name: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def sign_s3_url(self) -> "StudentWithClassOut":
        if self.profile_image_url:
            self.profile_image_url = sign_url(self.profile_image_url)
        return self


class StudentUpdate(BaseModel):
    name: str | None = None
    mobile_number: str | None = None
