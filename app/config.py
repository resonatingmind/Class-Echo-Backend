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

    # Cloudinary. Either paste the single CLOUDINARY_URL from the dashboard
    # (cloudinary://<api_key>:<api_secret>@<cloud_name>) or set the three parts
    # individually. When present, Cloudinary takes precedence over S3.
    CLOUDINARY_URL: str | None = None
    CLOUDINARY_CLOUD_NAME: str | None = None
    CLOUDINARY_API_KEY: str | None = None
    CLOUDINARY_API_SECRET: str | None = None
    # Folder (public_id prefix) every upload is nested under.
    CLOUDINARY_FOLDER: str = "classecho"

    GEMINI_API_KEY: str | None = None

    REDIS_URL: str = "redis://localhost:6379/0"

    ADMIN_NAME: str | None = None
    ADMIN_EMAIL: str | None = None
    ADMIN_PASSWORD: str | None = None

    # Email (invite / verification links). Mailjet is preferred; SMTP is the
    # fallback; with neither configured email_service logs the message instead
    # of sending — a safe default for local dev.
    #
    # MAILJET_FROM_EMAIL must be an address verified in the Mailjet dashboard
    # (Account Settings -> Sender domains & addresses), otherwise Mailjet accepts
    # the API call and then fails the message with "sender address not allowed".
    MAILJET_API_KEY: str | None = None
    MAILJET_API_SECRET: str | None = None
    MAILJET_FROM_EMAIL: str | None = None
    MAILJET_FROM_NAME: str = "Class Echo"
    # Validates credentials and payload against Mailjet without delivering.
    MAILJET_SANDBOX: bool = False

    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_EMAIL: str = "no-reply@classecho.app"
    FRONTEND_URL: str = "http://localhost:5173"

    @field_validator(
        "DATABASE_URL",
        "SECRET_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "AWS_STORAGE_BUCKET_NAME",
        "CLOUDINARY_URL",
        "CLOUDINARY_CLOUD_NAME",
        "CLOUDINARY_API_KEY",
        "CLOUDINARY_API_SECRET",
        "CLOUDINARY_FOLDER",
        "GEMINI_API_KEY",
        "REDIS_URL",
        "ADMIN_NAME",
        "ADMIN_EMAIL",
        "ADMIN_PASSWORD",
        "MAILJET_API_KEY",
        "MAILJET_API_SECRET",
        "MAILJET_FROM_EMAIL",
        "MAILJET_FROM_NAME",
        "SMTP_HOST",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "SMTP_FROM_EMAIL",
        "FRONTEND_URL",
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

    @property
    def mailjet_configured(self) -> bool:
        return bool(
            self.MAILJET_API_KEY and self.MAILJET_API_SECRET and self.MAILJET_FROM_EMAIL
        )

    @property
    def email_backend(self) -> str:
        """Which transport send_email() uses: mailjet, smtp, or log-only."""
        if self.mailjet_configured:
            return "mailjet"
        if self.SMTP_HOST:
            return "smtp"
        return "log"

    @property
    def cloudinary_configured(self) -> bool:
        if self.CLOUDINARY_URL:
            return True
        return bool(
            self.CLOUDINARY_CLOUD_NAME
            and self.CLOUDINARY_API_KEY
            and self.CLOUDINARY_API_SECRET
        )

    @property
    def s3_configured(self) -> bool:
        return bool(
            self.AWS_ACCESS_KEY_ID
            and self.AWS_SECRET_ACCESS_KEY
            and self.AWS_REGION
            and self.AWS_STORAGE_BUCKET_NAME
            and self.AWS_STORAGE_BUCKET_NAME != "test-bucket"
        )

    @property
    def storage_backend(self) -> str:
        """Which object store uploads go to: cloudinary, s3, or local disk."""
        if self.cloudinary_configured:
            return "cloudinary"
        if self.s3_configured:
            return "s3"
        return "local"

    @model_validator(mode="after")
    def validate_storage_config(self):
        # Deliberately does not raise. This used to hard-fail startup unless all
        # four AWS variables were present, which contradicted the local-disk
        # fallback that already existed in s3_service — and meant losing an AWS
        # account took the whole API down rather than degrading storage.
        if self.storage_backend == "local":
            import logging

            logging.getLogger("app.config").warning(
                "No object storage configured (set CLOUDINARY_URL or the AWS_* "
                "variables). Uploads will be written to ./uploads, which is "
                "ephemeral on most hosts and unreachable from other containers."
            )
        return self

    @model_validator(mode="after")
    def warn_unconfigured_email(self):
        # Same philosophy as storage config above: never hard-fail startup, but
        # do not let an invite-only product silently drop every invite either.
        if self.email_backend == "log":
            import logging

            logging.getLogger("app.config").warning(
                "No email provider configured (set MAILJET_API_KEY, "
                "MAILJET_API_SECRET and MAILJET_FROM_EMAIL, or the SMTP_* "
                "variables). Invite emails will only be written to the log."
            )
        elif self.mailjet_configured and self.MAILJET_SANDBOX:
            import logging

            logging.getLogger("app.config").warning(
                "MAILJET_SANDBOX is on: Mailjet will validate messages but "
                "deliver nothing. Unset it to send real invites."
            )
        return self

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
