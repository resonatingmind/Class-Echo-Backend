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
