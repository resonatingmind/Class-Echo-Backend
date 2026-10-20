from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    # Optional separate URL for Alembic/DDL. Needed on Supabase: the runtime app
    # can talk to the transaction pooler (port 6543), but DDL and Alembic's
    # version-table locking are only reliable over the session pooler
    # (port 5432) or a direct connection. Falls back to DATABASE_URL.
    MIGRATION_DATABASE_URL: str | None = None
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

    # Email (invite / verification links). Providers are tried in the order
    # Resend -> Brevo -> Mailjet -> SMTP, and with none configured email_service
    # logs the message instead of sending — a safe default for local dev.
    #
    # The two API providers come first because they are the only ones that work
    # on a host with outbound SMTP blocked (Render's free tier null-routes ports
    # 25, 465 and 587): their APIs are ordinary HTTPS on 443.
    #
    # Resend is first because it is the only one of the four that a new account
    # can start sending on without waiting for a human review — verifying the
    # domain is the whole approval process. RESEND_FROM_EMAIL must be on a
    # domain verified under Domains; Resend refuses anything else outright
    # rather than accepting the call and dropping the message.
    RESEND_API_KEY: str | None = None
    RESEND_FROM_EMAIL: str | None = None

    # BREVO_FROM_EMAIL must be verified under Senders, Domains & Dedicated IPs ->
    # Senders. Brevo accepts the API call for an unverified sender and then
    # fails the message, which looks identical to a delivery problem.
    BREVO_API_KEY: str | None = None
    BREVO_FROM_EMAIL: str | None = None

    # Provider-neutral display name. MAILJET_FROM_NAME is kept as the fallback
    # so existing deployments do not lose their sender name on upgrade.
    EMAIL_FROM_NAME: str | None = None

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
    # Defaults to production on purpose. This value is baked into emailed
    # invite and verification links, so a deployment that forgets to set it
    # sends links nobody can open — and the failure is invisible until a
    # recipient complains. A dev who gets a production link in their own inbox
    # is a far cheaper mistake, and local runs set this in .env anyway.
    FRONTEND_URL: str = "https://class-echo-frontend.vercel.app"

    # Forgot-password requests allowed per (email, client IP) pair within the
    # window. Counted per request, whether or not the email has an account, so
    # hitting the limit reveals nothing about who is registered.
    PASSWORD_RESET_LIMIT: int = 10
    PASSWORD_RESET_WINDOW_HOURS: int = 24

    @field_validator(
        "DATABASE_URL",
        "MIGRATION_DATABASE_URL",
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
        "RESEND_API_KEY",
        "RESEND_FROM_EMAIL",
        "BREVO_API_KEY",
        "BREVO_FROM_EMAIL",
        "EMAIL_FROM_NAME",
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
    def sync_migration_url(self) -> str:
        """
        Sync (psycopg2) URL for Alembic and the start-up readiness probe.

        Prefers MIGRATION_DATABASE_URL so the app can run over Supabase's
        transaction pooler while DDL goes through the session pooler.
        """
        url = self.MIGRATION_DATABASE_URL or self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)

    @property
    def db_is_local(self) -> bool:
        """
        True only when the database host cannot be a managed/remote Postgres.
        reset_db.py uses this to refuse to wipe Supabase.
        """
        host = (urlparse(self.sync_migration_url).hostname or "").lower()
        return host in {
            "",
            "localhost",
            "127.0.0.1",
            "::1",
            "host.docker.internal",
            # docker-compose service names
            "postgres",
            "db",
        }

    @property
    def mailjet_configured(self) -> bool:
        return bool(
            self.MAILJET_API_KEY and self.MAILJET_API_SECRET and self.MAILJET_FROM_EMAIL
        )

    @property
    def brevo_configured(self) -> bool:
        return bool(self.BREVO_API_KEY and self.BREVO_FROM_EMAIL)

    @property
    def resend_configured(self) -> bool:
        return bool(self.RESEND_API_KEY and self.RESEND_FROM_EMAIL)

    @property
    def email_backend(self) -> str:
        """Which transport send_email() uses: resend, brevo, mailjet, smtp, log.

        First match wins, so configuring a new provider is enough to switch —
        but the loser stays silently loaded, which is its own trap. Clear the
        variables of any provider you are not using; the admin health screen
        warns whenever more than one is set.
        """
        if self.resend_configured:
            return "resend"
        if self.brevo_configured:
            return "brevo"
        if self.mailjet_configured:
            return "mailjet"
        if self.SMTP_HOST:
            return "smtp"
        return "log"

    @property
    def sender_email(self) -> str | None:
        """The From address for whichever provider is active.

        Centralised because every caller that needed it was re-deriving the
        same ternary, and each new provider silently broke the ones that were
        not updated.
        """
        backend = self.email_backend
        if backend == "resend":
            return self.RESEND_FROM_EMAIL
        if backend == "brevo":
            return self.BREVO_FROM_EMAIL
        if backend == "mailjet":
            return self.MAILJET_FROM_EMAIL
        if backend == "smtp":
            return self.SMTP_FROM_EMAIL
        return None

    @property
    def sender_name_default(self) -> str:
        """Env-level sender name, before any runtime override."""
        return self.EMAIL_FROM_NAME or self.MAILJET_FROM_NAME

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
