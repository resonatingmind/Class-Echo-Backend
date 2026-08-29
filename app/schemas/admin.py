from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AdminProfileOut(BaseModel):
    id: int
    name: str
    email: str

    model_config = ConfigDict(from_attributes=True)


class EmailStatusOut(BaseModel):
    """Deployment-level email diagnostics.

    Exists because a misconfigured deployment fails silently: send_email falls
    back to logging, so invites look like they were sent while nothing leaves
    the server. Deliberately reports no secrets - only which backend is active
    and whether the pieces an invite needs are present.
    """

    backend: str  # "mailjet" | "smtp" | "log"
    delivers_email: bool
    from_email: str | None
    from_name: str | None
    sandbox: bool
    frontend_url: str
    sample_invite_link: str
    # Redacted identity of the API credentials actually loaded on this
    # deployment, so "I set the key" can be checked against what is in use.
    credentials: str | None = None
    warnings: list[str]


class EmailTestRequest(BaseModel):
    to_email: EmailStr


class EmailTestResult(BaseModel):
    """Outcome of a real send attempt, reported synchronously.

    email-status only reports what is configured. This reports what actually
    happened when a message was pushed to the provider, because the two differ
    in every way that matters: an unverified sender address, a revoked key or a
    sandbox flag all look like perfect configuration right up to the send.
    """

    delivered: bool
    backend: str
    to_email: str
    from_email: str | None
    # The provider's own words on failure — an unverified From address and a
    # bad API key are both "no email arrived" from the outside.
    error: str | None = None
    credentials: str | None = None
    detail: str


class SetPasswordRequest(BaseModel):
    """A super admin setting someone else's password directly.

    Exists because email delivery is not always available (or fast enough) when
    an organisation is being stood up, and an admin sitting with the user needs
    a way to hand them working credentials there and then.
    """

    new_password: str = Field(min_length=8, max_length=128)
    # The acting admin re-enters their own password: this endpoint hands over
    # another account, so a borrowed session alone must not be enough.
    admin_password: str
    # Off only when the intent is a password change for an account whose
    # suspended/rejected state should survive the reset.
    activate: bool = True


class CredentialUpdateOut(BaseModel):
    user_type: str  # "school_admin" | "teacher"
    user_id: int
    name: str
    email: str
    status: str
    activated: bool
    detail: str


# ── Operations: email log ────────────────────────────────────────────────────


class EmailLogEntry(BaseModel):
    """One recorded send attempt.

    Carries no message body by design — see the EmailLog model for why — so the
    UI resends by re-minting a token rather than replaying a stored link.
    """

    id: int
    to_email: str
    subject: str
    purpose: str
    status: str
    backend: str | None
    error: str | None
    school_id: int | None
    related_type: str | None
    related_id: int | None
    created_at: datetime
    updated_at: datetime | None
    # False when nothing here identifies an account to re-invite (a test send,
    # or a row from before the account was deleted), so the UI can grey the
    # button out instead of offering an action that will 400.
    can_resend: bool

    model_config = ConfigDict(from_attributes=True)


class EmailStatsOut(BaseModel):
    """Today's delivery totals, and how close they are to the provider's cap.

    The cap matters on SMTP: Gmail cuts a sender off for ~24h once the daily
    recipient count is passed, which during a bulk invite is a failure you want
    to see coming rather than discover afterwards.
    """

    sent_today: int
    failed_today: int
    skipped_today: int
    queued_now: int
    backend: str
    provider_daily_cap: int | None
    cap_note: str | None


# ── Operations: recording pipeline ───────────────────────────────────────────


class PipelineRecordingOut(BaseModel):
    """A recording as the operations screen needs it: what broke, and whose."""

    id: int
    status: str
    error_reason: str | None
    lecture_title: str | None
    chapter_name: str | None
    teacher_name: str | None
    subject_name: str | None
    school_id: int | None
    school_name: str | None
    uploaded_at: datetime
    processed_at: datetime | None
    # How long it has been sitting in its current state. A "processing" job that
    # is 4 minutes old is working; one that is 90 minutes old is dead.
    minutes_in_state: float


# ── System health ────────────────────────────────────────────────────────────


class HealthComponent(BaseModel):
    key: str
    label: str
    status: str  # ok | warn | error
    detail: str


class SystemHealthOut(BaseModel):
    """Every dependency an operator would otherwise SSH in to check."""

    healthy: bool
    components: list[HealthComponent]
    checked_at: datetime
