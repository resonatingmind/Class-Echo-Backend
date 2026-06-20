from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    # Default 90 days (set ACCESS_TOKEN_EXPIRE_MINUTES in env to override).
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 129_600

    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    AWS_REGION: str | None = None
    AWS_STORAGE_BUCKET_NAME: str | None = None

    REDIS_URL: str = "redis://localhost:6379/0"

    ADMIN_NAME: str | None = None
    ADMIN_EMAIL: str | None = None
    ADMIN_PASSWORD: str | None = None

    @field_validator(
        "DATABASE_URL",
        "SECRET_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "AWS_STORAGE_BUCKET_NAME",
        "REDIS_URL",
        "ADMIN_NAME",
        "ADMIN_EMAIL",
        "ADMIN_PASSWORD",
        mode="before",
    )
    @classmethod
    def strip_env_strings(cls, value: str | None):
        if isinstance(value, str):
            cleaned = value.strip()
            while cleaned and cleaned[0] in {'"', "'", "`"}:
                cleaned = cleaned[1:].strip()
            while cleaned and cleaned[-1] in {'"', "'", "`"}:
                cleaned = cleaned[:-1].strip()
            return cleaned
        return value

    @model_validator(mode="after")
    def validate_s3_config(self):
        missing = [
            name
            for name in (
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_REGION",
                "AWS_STORAGE_BUCKET_NAME",
            )
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(f"Missing S3 configuration: {', '.join(missing)}")

        return self

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
