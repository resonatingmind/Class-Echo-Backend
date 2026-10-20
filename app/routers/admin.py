import csv
import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.core.deps import get_admin
from app.core.security import hash_password, verify_password
from app.config import settings
from app.models.auth_token import AuthToken
from app.models.school import School
from app.models.school_admin import SchoolAdmin
from app.models.teacher import Teacher
from app.services import audit_service, email_service, invite_service, settings_service
from app.services import teacher_onboarding_service as onboarding
from app.models.class_ import Class
from app.models.subject import Subject
from app.models.student import Student
from app.models.recording import Recording
from app.models.llm_report import LLMReport
from app.models.audit_log import AuditLog
from app.models.chapter import Chapter
from app.models.chapter_chunk import ChapterChunk
from app.models.chapter_document import ChapterDocument
from app.models.timetable_entry import TimetableEntry
from app.models.email_log import EmailLog, EmailStatus
from app.models.recording import RecordingStatus
from app.schemas.school import (
    SchoolCreate,
    SchoolUpdate,
    SchoolOut,
    SchoolRejectRequest,
    SchoolDeleteRequest,
)
from app.schemas.admin import (
    AdminProfileOut,
    AuditLogEntry,
    CredentialUpdateOut,
    EmailLogEntry,
    EmailStatsOut,
    EmailStatusOut,
    EmailTestRequest,
    EmailTestResult,
    HealthComponent,
    PipelineRecordingOut,
    SetPasswordRequest,
    SettingOut,
    SettingUpdate,
    SystemHealthOut,
    UserSearchResult,
)
from app.schemas.teacher import (
    TeacherBulkInviteRequest,
    TeacherBulkInviteResponse,
    TeacherInviteEntry,
    TeacherInviteResult,
    TeacherOut,
)
from app.schemas.class_ import ClassOut
from app.schemas.subject import SubjectOut
from app.schemas.student import StudentOut
from app.schemas.recording import RecordingWithReport

router = APIRouter()


@router.get("/me", response_model=AdminProfileOut)
async def get_my_profile(admin=Depends(get_admin)):
    return admin


@router.get("/system/email-status", response_model=EmailStatusOut)
async def get_email_status(_=Depends(get_admin)):
    """Report whether this deployment can actually deliver invitations.

    An invite that is only written to the log looks identical to a delivered one
    from the UI, so this makes the difference visible without shell access to
    the server.
    """
    backend = settings.email_backend
    warnings: list[str] = []

    if backend == "log":
        warnings.append(
            "No email provider configured, so invitations are only written to "
            "the server log and never delivered. Set RESEND_API_KEY and "
            "RESEND_FROM_EMAIL on this deployment (or the BREVO_* / MAILJET_* / "
            "SMTP_* variables)."
        )
    if backend == "mailjet" and settings.MAILJET_SANDBOX:
        warnings.append(
            "MAILJET_SANDBOX is enabled: Mailjet validates messages but "
            "delivers nothing. Correct for staging, wrong for production."
        )
    # The effective values, not the raw environment ones: both are overridable
    # from the settings screen now, and reporting the env value here would send
    # someone hunting through Render for a number that is no longer in use.
    effective_frontend_url = invite_service.frontend_url()
    if "localhost" in effective_frontend_url or "127.0.0.1" in effective_frontend_url:
        warnings.append(
            f"The app URL is {effective_frontend_url!r}, so emailed invite links "
            "point at the server's own machine and will not open for recipients. "
            "Set it to the public URL of the app."
        )
    if email_service.email_is_paused():
        warnings.append(
            "Outgoing email is paused in Settings. Messages are recorded in the "
            "operations log and nothing is delivered."
        )

    return EmailStatusOut(
        backend=backend,
        delivers_email=backend != "log" and not email_service.email_is_paused(),
        from_email=(
            settings.sender_email
        ),
        from_name=email_service.sender_display_name(),
        sandbox=settings.MAILJET_SANDBOX,
        frontend_url=effective_frontend_url,
        sample_invite_link=invite_service.invite_link("EXAMPLE-TOKEN"),
        credentials=email_service.credential_fingerprint(),
        warnings=warnings,
    )


@router.post("/system/email-test", response_model=EmailTestResult)
async def send_test_email(
    body: EmailTestRequest,
    _=Depends(get_admin),
):
    """Send a real email now and report exactly what the provider said.

    Deliberately bypasses Celery and sends on this request: routing it through
    the worker would put any error in a log the person debugging cannot see,
    which is the situation this endpoint exists to escape.
    """
    from app.services.email_service import send_email

    backend = settings.email_backend
    credentials = email_service.credential_fingerprint()

    if backend == "log":
        return EmailTestResult(
            delivered=False,
            backend=backend,
            to_email=body.to_email,
            from_email=None,
            error="No email provider is configured.",
            detail=(
                "This deployment has no RESEND_*, BREVO_*, MAILJET_* or SMTP_* "
                "variables set, so every message is written to the server log and "
                "nothing is delivered. Nothing was sent."
            ),
        )

    from_email = (
        settings.sender_email
    )

    # Recorded like any other message so the operations log shows the test runs
    # interleaved with the real invitations — which is the comparison you
    # actually want when only some of them are arriving.
    from app.models.email_log import EmailStatus as _EmailStatus
    from app.services import email_log_service

    log_id = email_log_service.record(
        to_email=body.to_email,
        subject="Class Echo test email",
        purpose="test",
        backend=backend,
    )

    try:
        send_email(
            body.to_email,
            "Class Echo test email",
            "<p>This is a test message from Class Echo. If you can read it, "
            "invitations sent from this deployment will arrive too.</p>",
        )
    except Exception as exc:
        message = str(exc)
        email_log_service.mark(log_id, _EmailStatus.failed, message)
        lowered = message.lower()
        if backend == "resend" and "401" in message:
            detail = (
                "Resend did not accept this API key. Check the fingerprint "
                "below: a real key starts with 're_'. Keys are shown once at "
                "creation, so if yours was copied from the list afterwards it "
                "is truncated — create a new one."
            )
        elif backend == "resend" and (
            "403" in message or "domain" in lowered or "not verified" in lowered
        ):
            # Resend will not send from a domain it has not verified, and says
            # so directly. Unlike Brevo there is no review to wait on — this is
            # always DNS, and always fixable in minutes.
            detail = (
                f"Resend will not send from {settings.RESEND_FROM_EMAIL!r} "
                "because its domain is not verified yet. Open Resend → Domains "
                "and check the DNS records show as verified; propagation can "
                "take up to an hour. The sending address must be on that exact "
                "domain — a Gmail address will never work here."
            )
        elif backend == "resend":
            detail = "Resend refused the message. Its own error is above."
        elif backend == "brevo" and (
            "not yet activated" in lowered or "not been activated" in lowered
        ):
            # Checked before the permission_denied branch below, which this
            # error also matches. Brevo ships both an unverified sender and an
            # unactivated account under code "permission_denied", and treating
            # this one as a sender problem sends you to verify an address that
            # is already verified — the same misdiagnosis the Mailjet 401 caused.
            detail = (
                "Brevo has not activated transactional sending on this account "
                "yet. This is an account review, not a configuration fault: the "
                "key and sender are fine and nothing you change here will help. "
                "Open a support ticket from your Brevo account asking for "
                "transactional (not marketing) activation, and say what you "
                "send, roughly how much, and your site URL. Note that Brevo "
                "generally wants a verified domain before it activates, which a "
                "free Gmail sender address does not satisfy."
            )
        elif backend == "brevo" and "unauthorized" in lowered:
            # Brevo says "Key not found" for both a wrong key and one pasted
            # from the SMTP tab, which sits beside the API tab in its dashboard.
            detail = (
                "Brevo did not recognise this API key. Check the fingerprint "
                "below: a real key starts with 'xkeysib-'. If yours does not, "
                "you have most likely copied the SMTP relay password from the "
                "neighbouring tab rather than an API key. Brevo also masks keys "
                "after creation, so a key copied from the list rather than from "
                "the creation dialog is truncated and will never work — "
                "generate a fresh one."
            )
        elif backend == "brevo" and (
            "permission_denied" in lowered or "sender" in lowered
        ):
            # The single most common Brevo failure after setup: the API call is
            # authorised, the From address simply was never verified.
            detail = (
                f"Brevo accepted the key but refused the sender address "
                f"{settings.BREVO_FROM_EMAIL!r}. Verify it under Senders, "
                "Domains & Dedicated IPs → Senders — Brevo emails a "
                "confirmation code to that address, and until it is confirmed "
                "every send is rejected."
            )
        elif backend == "brevo":
            detail = (
                "Brevo refused the message. Its own error is above; the 'code' "
                "field in it is the part that matters."
            )
        elif "mj-0001" in message or "temporarily blocked" in lowered:
            # Not a credential fault at all: the key authenticates, the account
            # is barred from sending. Mailjet holds new accounts for a
            # compliance review, so this is the expected first-week failure and
            # no amount of re-pasting the key will clear it.
            detail = (
                "Mailjet authenticated the key but has blocked this account "
                "from sending, which it reports as a 401. The credentials are "
                "not the problem — re-pasting them will not help. Contact "
                "Mailjet support (Account settings shows the same notice) to "
                "get the account reviewed and unblocked. Until then, clear the "
                "MAILJET_* variables to fall back to logging invite links."
            )
        elif "401" in message or "API key" in message or "key/secret" in message:
            # A 401 says only that the pair is wrong, never how. These are the
            # three ways it actually happens, in the order they happen.
            detail = (
                "Mailjet rejected these credentials. Compare the key/secret "
                "fingerprint shown below against your Mailjet account "
                "(Account settings → API Key Management): a real key and "
                "secret are 32 characters each. Check for a truncated paste, "
                "the key and secret filled in the wrong way round, or a value "
                "from a different Mailjet sub-account. Note that variables "
                "added on the host only take effect after a redeploy."
            )
        else:
            detail = f"{backend} refused the message. Its own error is above."
        return EmailTestResult(
            delivered=False,
            backend=backend,
            to_email=body.to_email,
            from_email=from_email,
            error=message,
            credentials=credentials,
            detail=detail,
        )

    if backend == "mailjet" and settings.MAILJET_SANDBOX:
        # Mailjet accepts and validates the message, then throws it away, so a
        # clean success here still means no email arrives. "skipped" is the same
        # verdict the log gives an unconfigured deployment, for the same reason.
        email_log_service.mark(log_id, _EmailStatus.skipped)
        return EmailTestResult(
            delivered=False,
            backend=backend,
            to_email=body.to_email,
            from_email=from_email,
            credentials=credentials,
            error="MAILJET_SANDBOX is enabled.",
            detail=(
                "Mailjet accepted and validated the message but delivered "
                "nothing, which is what sandbox mode does. Set "
                "MAILJET_SANDBOX=false to send for real."
            ),
        )

    email_log_service.mark(log_id, _EmailStatus.sent)
    return EmailTestResult(
        delivered=True,
        backend=backend,
        to_email=body.to_email,
        from_email=from_email,
        credentials=credentials,
        detail=(
            f"{backend} accepted the message for {body.to_email}. If it does "
            "not arrive within a few minutes, check the spam folder and the "
            f"provider's own event log for {from_email}."
        ),
    )


# ── Operations & system diagnostics ──────────────────────────────────────────

# Gmail stops accepting mail from a sender for ~24h past this many recipients.
# Workspace accounts get 2000, but assuming the lower number is the safe
# direction to be wrong in: warning early costs nothing, warning late costs a
# day of invitations.
GMAIL_DAILY_CAP = 500
# Resend's free plan. The monthly allowance is 3,000, but 100/day is what
# actually bites: a month reading 12% used tells you nothing about whether
# today's bulk invite will go through.
RESEND_FREE_DAILY_CAP = 100
# A job still "processing" after this long is not slow, it is dead — the worker
# holding it died mid-task, so nothing will ever come back to mark it failed.
STUCK_PROCESSING_MINUTES = 30


def _status_value(value) -> str:
    """Enum columns arrive as the enum from the ORM and a str from raw SQL."""
    return value.value if hasattr(value, "value") else str(value)


def _can_resend(row: EmailLog) -> bool:
    """Only invitations can be re-sent, and only if we still know whose.

    Verification mails have no resend endpoint and test sends have no account
    behind them, so offering the button for either would produce a 400 on click.
    """
    return bool(
        row.purpose == "invite" and row.school_id and row.related_type and row.related_id
    )


def _email_log_entry(row: EmailLog) -> EmailLogEntry:
    return EmailLogEntry(
        id=row.id,
        to_email=row.to_email,
        subject=row.subject,
        purpose=row.purpose,
        status=_status_value(row.status),
        backend=row.backend,
        error=row.error,
        school_id=row.school_id,
        related_type=row.related_type,
        related_id=row.related_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        can_resend=_can_resend(row),
    )


@router.get("/system/email-log", response_model=list[EmailLogEntry])
async def list_email_log(
    status: str | None = Query(None, description="queued|sent|failed|skipped"),
    purpose: str | None = Query(None, description="invite|verification|test"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    """Every send attempt, newest first.

    This is the answer to "the teacher says no invitation arrived", which until
    now could only be investigated by reading server logs - assuming the process
    that wrote them was still alive to be read.
    """
    query = select(EmailLog).order_by(EmailLog.created_at.desc())

    if status:
        try:
            query = query.where(EmailLog.status == EmailStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown status {status!r}")
    if purpose:
        query = query.where(EmailLog.purpose == purpose)

    result = await db.execute(query.limit(limit).offset(offset))
    return [_email_log_entry(row) for row in result.scalars().all()]


@router.get("/system/email-stats", response_model=EmailStatsOut)
async def get_email_stats(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    """Delivery totals for the last 24 hours, against the provider's cap.

    A rolling window rather than a calendar day because that is how Gmail
    measures it, and being wrong about the boundary is how you get cut off
    halfway through inviting a school.
    """
    since = datetime.utcnow() - timedelta(hours=24)
    result = await db.execute(
        select(EmailLog.status, func.count())
        .where(EmailLog.created_at >= since)
        .group_by(EmailLog.status)
    )
    counts = {_status_value(status): total for status, total in result.all()}

    backend = settings.email_backend
    cap = None
    cap_note = None
    if backend == "resend":
        cap = RESEND_FREE_DAILY_CAP
        cap_note = (
            "Resend's free plan allows 100 emails a day and 3,000 a month, so "
            "the daily limit is the one you hit first — inviting a large school "
            "in one sitting can reach it on its own. Sends past the cap are "
            "rejected until the window rolls over."
        )
    elif backend == "smtp" and "gmail" in (settings.SMTP_HOST or "").lower():
        cap = GMAIL_DAILY_CAP
        cap_note = (
            "Gmail counts recipients, not messages, over a rolling 24 hours. "
            "Past the cap it refuses everything from this sender for about a "
            "day, and the retries will exhaust themselves against it."
        )
    elif backend == "log":
        cap_note = (
            "No provider is configured, so these were written to the server log "
            "and never sent."
        )

    return EmailStatsOut(
        sent_today=counts.get("sent", 0),
        failed_today=counts.get("failed", 0),
        skipped_today=counts.get("skipped", 0),
        queued_now=counts.get("queued", 0),
        backend=backend,
        provider_daily_cap=cap,
        cap_note=cap_note,
    )


@router.post("/system/email-log/{log_id}/resend", status_code=202)
async def resend_from_email_log(
    log_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    """Re-send the invitation a log row refers to.

    Mints a brand new token through the normal resend path rather than replaying
    whatever went out the first time. That is not only caution about storing live
    tokens: issue_invite_token burns the previous token on every re-issue, so a
    replayed link would already be dead.
    """
    row = await db.get(EmailLog, log_id)
    if not row:
        raise HTTPException(status_code=404, detail="No such email log entry")
    if not _can_resend(row):
        raise HTTPException(
            status_code=400,
            detail=(
                "Only invitations can be re-sent from here, and this row does "
                "not identify an account to re-invite."
            ),
        )

    if row.related_type == "school_admin":
        return await resend_school_admin_invite(row.school_id, db, admin)
    if row.related_type == "teacher":
        return await resend_teacher_invite(row.school_id, row.related_id, db, admin)

    raise HTTPException(
        status_code=400, detail=f"Cannot resend a {row.related_type!r} invitation"
    )


@router.get("/system/recordings", response_model=list[PipelineRecordingOut])
async def list_pipeline_recordings(
    status: str | None = Query(None, description="pending|processing|completed|failed"),
    problems_only: bool = Query(True),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    """Recordings the analysis pipeline has failed or abandoned.

    The data was always here - status and error_reason are columns on the table
    - there was simply no way to see it without a database client.
    """
    stuck_before = datetime.utcnow() - timedelta(minutes=STUCK_PROCESSING_MINUTES)

    query = (
        select(Recording)
        .options(
            selectinload(Recording.teacher).selectinload(Teacher.school),
            selectinload(Recording.subject),
        )
        .order_by(Recording.uploaded_at.desc())
    )

    if status:
        try:
            query = query.where(Recording.status == RecordingStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown status {status!r}")
    elif problems_only:
        # Failed jobs, plus anything that entered the pipeline and never came
        # back out. The second half is the one nobody notices, because a job
        # nothing is working on raises no error anywhere.
        query = query.where(
            or_(
                Recording.status == RecordingStatus.failed,
                and_(
                    Recording.status.in_(
                        [RecordingStatus.pending, RecordingStatus.processing]
                    ),
                    Recording.uploaded_at < stuck_before,
                ),
            )
        )

    result = await db.execute(query.limit(limit))
    now = datetime.utcnow()

    out: list[PipelineRecordingOut] = []
    for rec in result.scalars().all():
        teacher = rec.teacher
        school = teacher.school if teacher else None
        since = rec.processed_at or rec.uploaded_at
        out.append(
            PipelineRecordingOut(
                id=rec.id,
                status=_status_value(rec.status),
                error_reason=rec.error_reason,
                lecture_title=rec.lecture_title,
                chapter_name=rec.chapter_name,
                teacher_name=teacher.name if teacher else None,
                subject_name=rec.subject.name if rec.subject else None,
                school_id=school.id if school else None,
                school_name=school.name if school else None,
                uploaded_at=rec.uploaded_at,
                processed_at=rec.processed_at,
                minutes_in_state=round((now - since).total_seconds() / 60, 1),
            )
        )
    return out


@router.post("/system/recordings/{recording_id}/retry", status_code=202)
async def retry_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    """Put a failed or stuck recording back through analysis.

    Refuses when no worker is listening rather than calling .delay() anyway.
    Queuing into a broker nobody is reading is what produces a stuck job in the
    first place, and doing it from the screen built to clear stuck jobs would be
    a remarkable way to make the problem worse.
    """
    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="No such recording")

    if not email_service.worker_is_listening():
        raise HTTPException(
            status_code=409,
            detail=(
                "No Celery worker is listening, so a retry would sit in the "
                "queue unread. Start the worker (celery -A celery_worker."
                "celery_app worker) and try again."
            ),
        )

    audit_service.record(
        db,
        actor=admin,
        action="recording.retry",
        target_type="recording",
        target_id=recording.id,
        target_label=recording.lecture_title or recording.chapter_name,
        detail=f"Requeued for analysis from {_status_value(recording.status)}.",
    )
    recording.status = RecordingStatus.pending
    recording.error_reason = None
    recording.processed_at = None
    await db.commit()

    from app.tasks.llm_tasks import process_recording

    process_recording.delay(recording.id, recording.cloudinary_url)
    return {"detail": f"Recording {recording.id} has been queued for reanalysis."}


@router.get("/system/health", response_model=SystemHealthOut)
async def get_system_health(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    """Every dependency an operator would otherwise need a shell to check.

    Each component reports ok / warn / error, where warn means "running, but
    configured in a way that loses data or mail". A deployment can be entirely
    green on uptime checks while dropping every invitation it is handed.
    """
    components: list[HealthComponent] = []

    try:
        await db.execute(select(1))
        components.append(
            HealthComponent(
                key="database",
                label="Database",
                status="ok",
                detail="Reachable and accepting queries.",
            )
        )
    except Exception as exc:
        components.append(
            HealthComponent(
                key="database", label="Database", status="error", detail=str(exc)[:300]
            )
        )

    # Broker and worker are reported separately on purpose: Redis being healthy
    # is exactly what makes a missing worker invisible, because .delay() returns
    # successfully either way.
    queue_depth: int | None = None
    try:
        import redis

        client = redis.from_url(settings.REDIS_URL, socket_timeout=2)
        client.ping()
        queue_depth = client.llen("celery")
        components.append(
            HealthComponent(
                key="redis",
                label="Redis (broker)",
                status="ok",
                detail=f"Reachable. {queue_depth} task(s) waiting in the queue.",
            )
        )
    except Exception as exc:
        components.append(
            HealthComponent(
                key="redis",
                label="Redis (broker)",
                status="error",
                detail=(
                    f"Unreachable: {str(exc)[:200]}. Recordings and emails "
                    "cannot be queued."
                ),
            )
        )

    if email_service.worker_is_listening():
        detail = "At least one worker answered."
        if queue_depth:
            detail += f" {queue_depth} task(s) still waiting."
        components.append(
            HealthComponent(
                key="celery", label="Background worker", status="ok", detail=detail
            )
        )
    else:
        components.append(
            HealthComponent(
                key="celery",
                label="Background worker",
                status="error",
                detail=(
                    "No worker answered. Recording analysis will not run, and "
                    "email falls back to sending inline on the web process."
                ),
            )
        )

    email_backend = settings.email_backend
    if email_service.email_is_paused():
        # Checked before the provider: a paused deployment with perfect
        # credentials still delivers nothing, and reporting "Sending via smtp"
        # while every message is being held would be the more misleading answer.
        components.append(
            HealthComponent(
                key="email",
                label="Email",
                status="warn",
                detail=(
                    "Paused in Settings. Messages are recorded in the operations "
                    "log as skipped and nothing is delivered."
                ),
            )
        )
    elif email_backend == "log":
        components.append(
            HealthComponent(
                key="email",
                label="Email",
                status="error",
                detail=(
                    "No provider configured. Invitations are written to the "
                    "server log and never delivered."
                ),
            )
        )
    elif email_backend == "mailjet" and settings.MAILJET_SANDBOX:
        components.append(
            HealthComponent(
                key="email",
                label="Email",
                status="warn",
                detail="Mailjet sandbox is on: messages validate but never arrive.",
            )
        )
    else:
        components.append(
            HealthComponent(
                key="email",
                label="Email",
                status="ok",
                detail=f"Sending via {email_backend}.",
            )
        )

    # Only the winner of the precedence order is ever used, and the losers give
    # no sign of being ignored — adding SMTP alongside a configured Mailjet
    # looks like a switch and changes nothing. Say so out loud.
    configured = []
    if settings.resend_configured:
        configured.append("Resend")
    if settings.brevo_configured:
        configured.append("Brevo")
    if settings.mailjet_configured:
        configured.append("Mailjet")
    if settings.SMTP_HOST:
        configured.append("SMTP")
    if len(configured) > 1:
        shadowed = ", ".join(configured[1:])
        components.append(
            HealthComponent(
                key="email_precedence",
                label="Email provider precedence",
                status="warn",
                detail=(
                    f"{len(configured)} providers are configured. {configured[0]} "
                    f"wins and {shadowed} {'is' if len(configured) == 2 else 'are'} "
                    "ignored entirely. Clear the unused provider's variables so "
                    "the configuration matches what is actually sending."
                ),
            )
        )

    storage = settings.storage_backend
    components.append(
        HealthComponent(
            key="storage",
            label="File storage",
            status="ok" if storage != "local" else "error",
            detail=(
                f"Uploads go to {storage}."
                if storage != "local"
                else (
                    "Uploads are written to ./uploads on the web container, "
                    "which is wiped on every redeploy and invisible to the worker."
                )
            ),
        )
    )

    components.append(
        HealthComponent(
            key="gemini",
            label="AI (Gemini)",
            status="ok" if settings.GEMINI_API_KEY else "error",
            detail=(
                "API key present."
                if settings.GEMINI_API_KEY
                else "GEMINI_API_KEY is unset, so recording analysis cannot run."
            ),
        )
    )

    try:
        maintenance = bool(settings_service.get("maintenance_mode"))
    except Exception:
        maintenance = False
    components.append(
        HealthComponent(
            key="maintenance",
            label="Maintenance mode",
            status="warn" if maintenance else "ok",
            detail=(
                "On. Teachers, students and school admins cannot save anything; "
                "reads still work and this panel is unaffected."
                if maintenance
                else "Off. The app is accepting changes normally."
            ),
        )
    )

    frontend_url = invite_service.frontend_url()
    local_frontend = "localhost" in frontend_url or "127.0.0.1" in frontend_url
    components.append(
        HealthComponent(
            key="frontend_url",
            label="Invite link target",
            status="warn" if local_frontend else "ok",
            detail=(
                f"FRONTEND_URL is {frontend_url}, so emailed links point at the "
                "server's own machine and will not open for recipients."
                if local_frontend
                else f"Links are built against {frontend_url}."
            ),
        )
    )

    return SystemHealthOut(
        healthy=all(c.status == "ok" for c in components),
        components=components,
        checked_at=datetime.utcnow(),
    )


# ── Audit log ────────────────────────────────────────────────────────────────


@router.get("/system/audit-log", response_model=list[AuditLogEntry])
async def list_audit_log(
    action: str | None = Query(None, description="Exact action, e.g. school.suspend"),
    school_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    """What super admins have done, newest first.

    Read-only by design, and there is deliberately no delete endpoint: a log the
    people it records can edit is not evidence of anything.
    """
    query = select(AuditLog).order_by(AuditLog.created_at.desc())
    if action:
        query = query.where(AuditLog.action == action)
    if school_id is not None:
        query = query.where(AuditLog.school_id == school_id)

    result = await db.execute(query.limit(limit).offset(offset))
    return list(result.scalars().all())


# ── School data export ───────────────────────────────────────────────────────


EXPORTABLE_RESOURCES = ("teachers", "classes", "recordings")


def _csv_response(filename: str, header: list[str], rows: list[list]) -> StreamingResponse:
    """Build a CSV download from rows already in memory.

    Streamed from a StringIO rather than generated lazily: a single school's
    teachers or recordings are thousands of rows at the very most, and holding
    them is far cheaper than keeping a database session open across the response.

    newline="" is what csv.writer expects; without it every row gains a stray
    blank line on Windows clients.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/schools/{school_id}/export")
async def export_school_data(
    school_id: int,
    resource: str = Query("teachers", description="teachers | classes | recordings"),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    """Download one school's records as CSV.

    One resource per file rather than everything in one: a single CSV holding
    teachers and recordings together would need a record-type column and a union
    of both schemas, which is worse to read and worse to open in a spreadsheet
    than three clean files.
    """
    if resource not in EXPORTABLE_RESOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"resource must be one of {', '.join(EXPORTABLE_RESOURCES)}",
        )

    school = await db.get(School, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    safe_name = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in school.name
    ).strip("_") or f"school-{school_id}"

    if resource == "teachers":
        result = await db.execute(
            select(Teacher).where(Teacher.school_id == school_id).order_by(Teacher.name)
        )
        rows = [
            [
                t.id,
                t.name,
                t.email,
                t.status,
                "yes" if t.hashed_password else "no",
                "yes" if t.email_verified else "no",
                t.created_at.isoformat() if t.created_at else "",
            ]
            for t in result.scalars().all()
        ]
        return _csv_response(
            f"{safe_name}-teachers.csv",
            ["id", "name", "email", "status", "password_set", "email_verified", "created_at"],
            rows,
        )

    if resource == "classes":
        result = await db.execute(
            select(Class)
            .options(selectinload(Class.subjects).selectinload(Subject.teacher))
            .where(Class.school_id == school_id)
            .order_by(Class.name)
        )
        rows = []
        for cls in result.scalars().all():
            subjects = list(cls.subjects or [])
            if not subjects:
                # A class with no subjects still belongs in the export; dropping
                # it would make the file disagree with the app's own class list.
                rows.append([cls.id, cls.name, cls.section or "", "", ""])
                continue
            for subject in subjects:
                rows.append(
                    [
                        cls.id,
                        cls.name,
                        cls.section or "",
                        subject.name,
                        subject.teacher.name if subject.teacher else "",
                    ]
                )
        return _csv_response(
            f"{safe_name}-classes.csv",
            ["class_id", "class_name", "section", "subject", "teacher"],
            rows,
        )

    class_ids = select(Class.id).where(Class.school_id == school_id)
    subject_ids = select(Subject.id).where(Subject.class_id.in_(class_ids))
    result = await db.execute(
        select(Recording)
        .options(selectinload(Recording.teacher), selectinload(Recording.subject))
        .where(Recording.subject_id.in_(subject_ids))
        .order_by(Recording.uploaded_at.desc())
    )
    rows = [
        [
            r.id,
            r.lecture_title or "",
            r.chapter_name or "",
            r.subject.name if r.subject else "",
            r.teacher.name if r.teacher else "",
            _status_value(r.status),
            round(r.duration_seconds or 0, 1),
            r.uploaded_at.isoformat() if r.uploaded_at else "",
            r.processed_at.isoformat() if r.processed_at else "",
            r.error_reason or "",
        ]
        for r in result.scalars().all()
    ]
    return _csv_response(
        f"{safe_name}-recordings.csv",
        [
            "id",
            "lecture_title",
            "chapter",
            "subject",
            "teacher",
            "status",
            "duration_seconds",
            "uploaded_at",
            "processed_at",
            "error_reason",
        ],
        rows,
    )


# ── Runtime settings ─────────────────────────────────────────────────────────


@router.get("/system/settings", response_model=list[SettingOut])
async def list_system_settings(_=Depends(get_admin)):
    """Every setting that can be changed without a redeploy.

    Credentials are deliberately absent: those stay in the environment, where a
    bad value can be corrected by a deploy rather than locking you out of the
    screen you would use to correct it.
    """
    return [SettingOut(**row) for row in settings_service.describe()]


@router.put("/system/settings/{key}", response_model=SettingOut)
async def update_system_setting(
    key: str,
    body: SettingUpdate,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    try:
        previous = settings_service.get(key)
        coerced = settings_service.set_value(key, body.value, updated_by=admin.email)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown setting {key!r}")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Settings changes alter behaviour everywhere at once with no code change to
    # point at afterwards, which makes them the easiest thing in the system to
    # forget having done. Both values are recorded, because "who turned this on"
    # and "what was it before" are usually the same question.
    audit_service.record(
        db,
        actor=admin,
        action="setting.update",
        target_type="setting",
        target_label=key,
        detail=f"{previous!r} -> {coerced!r}",
    )
    await db.commit()

    return _one_setting(key)


@router.post("/system/settings/{key}/reset", response_model=SettingOut)
async def reset_system_setting(
    key: str,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    """Drop the override so the environment variable applies again."""
    try:
        previous = settings_service.get(key)
        restored = settings_service.reset(key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown setting {key!r}")

    audit_service.record(
        db,
        actor=admin,
        action="setting.reset",
        target_type="setting",
        target_label=key,
        detail=f"{previous!r} -> environment default {restored!r}",
    )
    await db.commit()
    return _one_setting(key)


def _one_setting(key: str) -> SettingOut:
    for row in settings_service.describe():
        if row["key"] == key:
            return SettingOut(**row)
    raise HTTPException(status_code=404, detail=f"Unknown setting {key!r}")


# ── Global user lookup ───────────────────────────────────────────────────────


@router.get("/users/search", response_model=list[UserSearchResult])
async def search_users(
    q: str = Query(..., min_length=2, description="Email or name fragment"),
    limit: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    """Find anyone across every school, by email or name.

    The per-school screens already expose all of this, but only once you know
    which school to open. A support call starts with an email address and
    nothing else, so this is the lookup that matches how the question arrives.
    """
    pattern = f"%{q.strip().lower()}%"
    results: list[UserSearchResult] = []

    admins = await db.execute(
        select(SchoolAdmin)
        .options(selectinload(SchoolAdmin.school))
        .where(
            or_(
                func.lower(SchoolAdmin.email).like(pattern),
                func.lower(SchoolAdmin.name).like(pattern),
            )
        )
        .limit(limit)
    )
    for row in admins.scalars().all():
        results.append(
            UserSearchResult(
                id=row.id,
                name=row.name,
                email=row.email,
                role="school_admin",
                status=getattr(row, "status", None),
                school_id=row.school_id,
                school_name=row.school.name if row.school else None,
                has_password=bool(row.hashed_password),
                email_verified=getattr(row, "email_verified", None),
                created_at=getattr(row, "created_at", None),
                can_resend_invite=(
                    getattr(row, "status", None) == "invited" and row.school_id is not None
                ),
            )
        )

    teachers = await db.execute(
        select(Teacher)
        .options(selectinload(Teacher.school))
        .where(
            or_(
                func.lower(Teacher.email).like(pattern),
                func.lower(Teacher.name).like(pattern),
            )
        )
        .limit(limit)
    )
    for row in teachers.scalars().all():
        results.append(
            UserSearchResult(
                id=row.id,
                name=row.name,
                email=row.email,
                role="teacher",
                status=row.status,
                school_id=row.school_id,
                school_name=row.school.name if row.school else None,
                has_password=bool(row.hashed_password),
                email_verified=row.email_verified,
                created_at=row.created_at,
                can_resend_invite=(row.status == "invited" and row.school_id is not None),
            )
        )

    students = await db.execute(
        select(Student)
        .options(selectinload(Student.school))
        .where(
            or_(
                func.lower(Student.email).like(pattern),
                func.lower(Student.name).like(pattern),
            )
        )
        .limit(limit)
    )
    for row in students.scalars().all():
        results.append(
            UserSearchResult(
                id=row.id,
                name=row.name,
                email=row.email,
                role="student",
                # Students sign themselves up and have no invite lifecycle, so
                # there is no status column to report rather than a missing one.
                status=None,
                school_id=row.school_id,
                school_name=row.school.name if row.school else None,
                has_password=bool(row.hashed_password),
                email_verified=None,
                created_at=row.created_at,
                can_resend_invite=False,
            )
        )

    # Exact address first: searching an email should not bury the person who
    # owns it under everyone whose name happens to contain the same letters.
    needle = q.strip().lower()
    results.sort(key=lambda r: (r.email.lower() != needle, r.role, r.email.lower()))
    return results[:limit]


@router.get("/schools", response_model=list[SchoolOut])
async def list_schools(
    status: str | None = Query(None, description="Filter by invited/pending_approval/active/suspended/rejected"),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    query = select(School).options(selectinload(School.admin)).order_by(School.created_at.desc())
    if status is not None:
        query = query.join(SchoolAdmin, School.id == SchoolAdmin.school_id).where(
            SchoolAdmin.status == status
        )
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/schools", response_model=SchoolOut, status_code=201)
async def create_school(
    body: SchoolCreate,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    # Check admin email not already taken
    from app.models.admin import Admin
    from app.models.teacher import Teacher
    from app.models.student import Student
    for Model in (Admin, SchoolAdmin, Teacher, Student):
        r = await db.execute(select(Model).where(Model.email == body.admin_email))
        if r.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Admin email already registered")

    school = School(name=body.name, address=body.address, logo_url=body.logo_url)
    db.add(school)
    await db.flush()  # get school.id

    school_admin = SchoolAdmin(
        name=body.admin_name,
        email=body.admin_email,
        hashed_password=None,
        school_id=school.id,
        status="invited",
        invited_by_admin_id=admin.id,
    )
    db.add(school_admin)
    await db.flush()  # get school_admin.id

    admin_token = await invite_service.issue_invite_token(db, "school_admin", school_admin.id)

    # Teachers named at creation time are invited in the same transaction, so a
    # failure part-way through does not leave a half-populated organisation.
    teacher_invites: list[tuple[Teacher, str]] = []
    for entry in body.teachers:
        action, existing = await onboarding.classify_teacher_signup(db, entry.email, school.id)
        if action != onboarding.CREATE_NEW:
            # A brand new organisation cannot legitimately collide with itself,
            # so this only fires when the address belongs to another account.
            raise HTTPException(
                status_code=409,
                detail=f"{entry.email} is already registered on Class Echo",
            )
        teacher = Teacher(
            name=entry.name,
            email=entry.email,
            hashed_password=None,
            school_id=school.id,
            status="invited",
        )
        db.add(teacher)
        await db.flush()
        token = await invite_service.issue_invite_token(db, "teacher", teacher.id)
        teacher_invites.append((teacher, token))

    await db.commit()
    await db.refresh(school)

    # Emails are queued only after the commit: queuing first can deliver an
    # invite link whose account the transaction then rolls back.
    invite_service.queue_invite_email(
        to_email=school_admin.email,
        to_name=school_admin.name,
        organisation_name=school.name,
        role_label="school admin",
        raw_token=admin_token,
        inviter_name=admin.name,
        school_id=school.id,
        related_type="school_admin",
        related_id=school_admin.id,
    )
    for teacher, token in teacher_invites:
        invite_service.queue_invite_email(
            to_email=teacher.email,
            to_name=teacher.name,
            organisation_name=school.name,
            role_label="teacher",
            raw_token=token,
            inviter_name=admin.name,
            school_id=school.id,
            related_type="teacher",
            related_id=teacher.id,
        )

    audit_service.record(
        db,
        actor=admin,
        action="school.create",
        target_type="school",
        target_id=school.id,
        target_label=school.name,
        school_id=school.id,
        detail=(
            f"Organisation created with admin {school_admin.email} and "
            f"{len(teacher_invites)} teacher invitation(s)."
        ),
    )
    await db.commit()

    result = await db.execute(
        select(School).options(selectinload(School.admin)).where(School.id == school.id)
    )
    return result.scalar_one()


@router.get("/schools/{school_id}", response_model=SchoolOut)
async def get_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    result = await db.execute(
        select(School).options(selectinload(School.admin)).where(School.id == school_id)
    )
    school = result.scalar_one_or_none()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    return school


@router.put("/schools/{school_id}", response_model=SchoolOut)
async def update_school(
    school_id: int,
    body: SchoolUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    result = await db.execute(select(School).where(School.id == school_id))
    school = result.scalar_one_or_none()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(school, field, value)
    await db.commit()
    await db.refresh(school)

    result = await db.execute(
        select(School).options(selectinload(School.admin)).where(School.id == school.id)
    )
    return result.scalar_one()


@router.delete("/schools/{school_id}", status_code=204)
async def delete_school(
    school_id: int,
    body: SchoolDeleteRequest,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    if not verify_password(body.password, admin.hashed_password):
        raise HTTPException(status_code=403, detail="Incorrect password")

    result = await db.execute(select(School).where(School.id == school_id))
    school = result.scalar_one_or_none()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    # Every table below is wired with ON DELETE NO ACTION, so the order here is
    # not cosmetic: each row has to go before whatever it points at. Deleting a
    # school used to fail outright because chapters, timetable entries and
    # teachers were never cleared and still referenced the rows being removed.
    class_ids = select(Class.id).where(Class.school_id == school_id)
    subject_ids = select(Subject.id).where(Subject.class_id.in_(class_ids))
    chapter_ids = select(Chapter.id).where(Chapter.subject_id.in_(subject_ids))
    teacher_ids = select(Teacher.id).where(Teacher.school_id == school_id)
    # A recording is reachable either through the school's subjects or through
    # one of its teachers; both have to go before subjects and teachers do.
    recording_ids = select(Recording.id).where(
        or_(
            Recording.subject_id.in_(subject_ids),
            Recording.teacher_id.in_(teacher_ids),
        )
    )

    await db.execute(
        delete(LLMReport).where(LLMReport.recording_id.in_(recording_ids))
    )
    await db.execute(
        delete(Recording).where(Recording.id.in_(recording_ids))
    )
    # chapter_knowledge and learning_plans cascade from chapters; chunks and
    # documents do not, so they are cleared explicitly first.
    await db.execute(
        delete(ChapterChunk).where(ChapterChunk.chapter_id.in_(chapter_ids))
    )
    await db.execute(
        delete(ChapterDocument).where(ChapterDocument.chapter_id.in_(chapter_ids))
    )
    await db.execute(
        delete(Chapter).where(Chapter.id.in_(chapter_ids))
    )
    # Points at class, subject and teacher at once, so it goes before all three.
    await db.execute(
        delete(TimetableEntry).where(TimetableEntry.school_id == school_id)
    )
    await db.execute(
        delete(Subject).where(Subject.id.in_(subject_ids))
    )
    await db.execute(
        delete(Student).where(Student.school_id == school_id)
    )
    await db.execute(
        delete(Class).where(Class.school_id == school_id)
    )
    # Invite and verification tokens are addressed by (user_type, user_id) with
    # no foreign key, so they outlive the accounts unless cleared here - and an
    # unredeemed invite for a deleted teacher would still activate whatever
    # account later takes that id. Must run while those ids still resolve.
    school_admin_ids = select(SchoolAdmin.id).where(SchoolAdmin.school_id == school_id)
    await db.execute(
        delete(AuthToken).where(
            or_(
                and_(
                    AuthToken.user_type == "teacher",
                    AuthToken.user_id.in_(teacher_ids),
                ),
                and_(
                    AuthToken.user_type == "school_admin",
                    AuthToken.user_id.in_(school_admin_ids),
                ),
            )
        )
    )
    # Teachers carry approved_by / invited_by references to school admins, so
    # they have to be removed before the admins that invited them.
    await db.execute(
        delete(Teacher).where(Teacher.school_id == school_id)
    )
    await db.execute(
        delete(SchoolAdmin).where(SchoolAdmin.school_id == school_id)
    )
    # Staged before the delete commits, so the record of the deletion shares its
    # transaction. The school name is captured here because in a moment it will
    # not exist anywhere else.
    audit_service.record(
        db,
        actor=admin,
        action="school.delete",
        target_type="school",
        target_id=school_id,
        target_label=school.name,
        school_id=school_id,
        detail="School deleted with all its classes, subjects, students, recordings and reports.",
    )
    await db.delete(school)
    await db.commit()


@router.post("/schools/{school_id}/approve", response_model=SchoolOut)
async def approve_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    school_admin = await _get_school_admin_for_school(school_id, db)
    if school_admin.status != "pending_approval":
        raise HTTPException(status_code=400, detail="Only pending schools can be approved")

    school_admin.status = "active"
    school_admin.approved_at = datetime.utcnow()
    school_admin.approved_by_admin_id = admin.id
    audit_service.record(
        db,
        actor=admin,
        action="school.approve",
        target_type="school",
        target_id=school_id,
        target_label=school_admin.email,
        school_id=school_id,
    )
    await db.commit()
    return await _get_school_with_admin(school_id, db)


@router.post("/schools/{school_id}/reject", response_model=SchoolOut)
async def reject_school(
    school_id: int,
    body: SchoolRejectRequest,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    school_admin = await _get_school_admin_for_school(school_id, db)
    if school_admin.status != "pending_approval":
        raise HTTPException(status_code=400, detail="Only pending schools can be rejected")

    school_admin.status = "rejected"
    school_admin.rejected_reason = body.reason
    audit_service.record(
        db,
        actor=admin,
        action="school.reject",
        target_type="school",
        target_id=school_id,
        target_label=school_admin.email,
        school_id=school_id,
        detail=body.reason,
    )
    await db.commit()
    return await _get_school_with_admin(school_id, db)


@router.post("/schools/{school_id}/suspend", response_model=SchoolOut)
async def suspend_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    school_admin = await _get_school_admin_for_school(school_id, db)
    if school_admin.status != "active":
        raise HTTPException(status_code=400, detail="Only active schools can be suspended")

    school_admin.status = "suspended"
    audit_service.record(
        db,
        actor=admin,
        action="school.suspend",
        target_type="school",
        target_id=school_id,
        target_label=school_admin.email,
        school_id=school_id,
    )
    await db.commit()
    return await _get_school_with_admin(school_id, db)


@router.post("/schools/{school_id}/reactivate", response_model=SchoolOut)
async def reactivate_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    school_admin = await _get_school_admin_for_school(school_id, db)
    if school_admin.status != "suspended":
        raise HTTPException(status_code=400, detail="Only suspended schools can be reactivated")

    school_admin.status = "active"
    audit_service.record(
        db,
        actor=admin,
        action="school.reactivate",
        target_type="school",
        target_id=school_id,
        target_label=school_admin.email,
        school_id=school_id,
    )
    await db.commit()
    return await _get_school_with_admin(school_id, db)


async def _get_school_admin_for_school(school_id: int, db: AsyncSession) -> SchoolAdmin:
    result = await db.execute(select(SchoolAdmin).where(SchoolAdmin.school_id == school_id))
    school_admin = result.scalar_one_or_none()
    if not school_admin:
        raise HTTPException(status_code=404, detail="School not found")
    return school_admin


async def _get_school_with_admin(school_id: int, db: AsyncSession) -> School:
    result = await db.execute(
        select(School).options(selectinload(School.admin)).where(School.id == school_id)
    )
    return result.scalar_one()


@router.get("/schools/{school_id}/teachers", response_model=list[TeacherOut])
async def list_school_teachers(
    school_id: int,
    status: str | None = Query(
        None, description="Filter by invited/pending_approval/active/suspended/rejected"
    ),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    await _get_school_with_admin(school_id, db)

    query = (
        select(Teacher)
        .options(selectinload(Teacher.school))
        .where(Teacher.school_id == school_id)
        .order_by(Teacher.created_at.desc())
    )
    if status is not None:
        query = query.where(Teacher.status == status)

    result = await db.execute(query)
    return [_teacher_out(t) for t in result.scalars().all()]


@router.post(
    "/schools/{school_id}/teachers/invite",
    response_model=TeacherBulkInviteResponse,
    status_code=201,
)
async def invite_school_teachers(
    school_id: int,
    body: TeacherBulkInviteRequest,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    """Invite one or many teachers straight into an organisation.

    Partial success is deliberate: pasting in a staff list where two addresses
    already exist should still invite everyone else, and report what it skipped,
    rather than rejecting the whole batch.
    """
    school = await _get_school_with_admin(school_id, db)

    results: list[TeacherInviteResult] = []
    pending: list[tuple[Teacher, str, TeacherInviteEntry]] = []
    seen_emails: set[str] = set()

    for entry in body.teachers:
        email = entry.email.lower()
        if email in seen_emails:
            results.append(
                TeacherInviteResult(
                    email=entry.email,
                    name=entry.name,
                    outcome="skipped",
                    reason="Duplicate address in this request",
                )
            )
            continue
        seen_emails.add(email)

        action, existing = await onboarding.classify_teacher_signup(db, entry.email, school_id)

        if action == onboarding.CONFLICT_TAKEN:
            results.append(
                TeacherInviteResult(
                    email=entry.email,
                    name=entry.name,
                    outcome="skipped",
                    reason="Already registered on Class Echo",
                    teacher_id=existing.id if existing else None,
                )
            )
            continue
        if action == onboarding.CONFLICT_OTHER_SCHOOL:
            results.append(
                TeacherInviteResult(
                    email=entry.email,
                    name=entry.name,
                    outcome="skipped",
                    reason="Already belongs to a different organisation",
                )
            )
            continue

        if action in (
            onboarding.EXISTING_INVITED_SAME_SCHOOL,
            onboarding.EXISTING_PENDING_SAME_SCHOOL,
        ):
            teacher = existing
            teacher.name = entry.name
            # A super-admin invite supersedes a self-registration awaiting
            # approval: accepting the emailed link is proof enough.
            teacher.status = "invited"
            outcome = "reinvited"
        else:
            teacher = Teacher(
                name=entry.name,
                email=entry.email,
                hashed_password=None,
                school_id=school_id,
                status="invited",
            )
            db.add(teacher)
            outcome = "invited"

        await db.flush()
        token = await invite_service.issue_invite_token(db, "teacher", teacher.id)
        pending.append((teacher, token, entry))
        results.append(
            TeacherInviteResult(
                email=entry.email,
                name=entry.name,
                outcome=outcome,
                teacher_id=teacher.id,
            )
        )

    await db.commit()

    for teacher, token, _entry in pending:
        invite_service.queue_invite_email(
            to_email=teacher.email,
            to_name=teacher.name,
            organisation_name=school.name,
            role_label="teacher",
            raw_token=token,
            inviter_name=admin.name,
            school_id=school.id,
            related_type="teacher",
            related_id=teacher.id,
        )

    # The per-row outcomes are otherwise a toast that disappears: a bulk invite
    # of forty teachers where six were skipped leaves nothing behind saying
    # which six, and "did that one get invited or not" then has no answer.
    # Recorded as one entry rather than forty, so a single paste of a staff list
    # reads as a single event.
    skipped = [r for r in results if r.outcome == "skipped"]
    audit_service.record(
        db,
        actor=admin,
        action="teacher.invite_bulk",
        target_type="school",
        target_id=school.id,
        target_label=school.name,
        school_id=school.id,
        detail=(
            f"{len(pending)} invited, {len(skipped)} skipped."
            + (
                " Skipped: "
                + "; ".join(f"{r.email} ({r.reason or 'no reason given'})" for r in skipped)
                if skipped
                else ""
            )
        ),
    )
    await db.commit()

    return TeacherBulkInviteResponse(
        invited_count=len(pending),
        skipped_count=len(results) - len(pending),
        results=results,
    )


@router.post("/schools/{school_id}/admin/resend-invite", status_code=202)
async def resend_school_admin_invite(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    school = await _get_school_with_admin(school_id, db)
    school_admin = await _get_school_admin_for_school(school_id, db)

    if school_admin.status != "invited":
        raise HTTPException(
            status_code=409,
            detail="This admin has already accepted their invitation",
        )

    token = await invite_service.issue_invite_token(db, "school_admin", school_admin.id)
    await db.commit()

    invite_service.queue_invite_email(
        to_email=school_admin.email,
        to_name=school_admin.name,
        organisation_name=school.name,
        role_label="school admin",
        raw_token=token,
        inviter_name=admin.name,
        school_id=school.id,
        related_type="school_admin",
        related_id=school_admin.id,
    )
    audit_service.record(
        db,
        actor=admin,
        action="school_admin.resend_invite",
        target_type="school_admin",
        target_id=school_admin.id,
        target_label=school_admin.email,
        school_id=school_id,
        detail="A fresh invitation was issued; the previous link was invalidated.",
    )
    await db.commit()
    return {"detail": f"Invitation resent to {school_admin.email}"}


@router.post("/schools/{school_id}/teachers/{teacher_id}/resend-invite", status_code=202)
async def resend_teacher_invite(
    school_id: int,
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    school = await _get_school_with_admin(school_id, db)

    result = await db.execute(
        select(Teacher).where(Teacher.id == teacher_id, Teacher.school_id == school_id)
    )
    teacher = result.scalar_one_or_none()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found in this organisation")
    if teacher.status != "invited":
        raise HTTPException(
            status_code=409,
            detail="This teacher has already accepted their invitation",
        )

    token = await invite_service.issue_invite_token(db, "teacher", teacher.id)
    await db.commit()

    invite_service.queue_invite_email(
        to_email=teacher.email,
        to_name=teacher.name,
        organisation_name=school.name,
        role_label="teacher",
        raw_token=token,
        inviter_name=admin.name,
        school_id=school.id,
        related_type="teacher",
        related_id=teacher.id,
    )
    audit_service.record(
        db,
        actor=admin,
        action="teacher.resend_invite",
        target_type="teacher",
        target_id=teacher.id,
        target_label=teacher.email,
        school_id=school_id,
        detail="A fresh invitation was issued; the previous link was invalidated.",
    )
    await db.commit()
    return {"detail": f"Invitation resent to {teacher.email}"}


@router.delete("/schools/{school_id}/teachers/{teacher_id}", status_code=204)
async def revoke_teacher_invite(
    school_id: int,
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    """Withdraw an invitation that has not been accepted yet.

    Restricted to invited accounts: deleting an active teacher would orphan
    their recordings, which is what the suspend flow is for.
    """
    result = await db.execute(
        select(Teacher).where(Teacher.id == teacher_id, Teacher.school_id == school_id)
    )
    teacher = result.scalar_one_or_none()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found in this organisation")
    if teacher.status != "invited":
        raise HTTPException(
            status_code=409,
            detail="Only pending invitations can be revoked",
        )

    await db.execute(
        delete(AuthToken).where(
            AuthToken.user_type == "teacher", AuthToken.user_id == teacher.id
        )
    )
    audit_service.record(
        db,
        actor=admin,
        action="teacher.revoke_invite",
        target_type="teacher",
        target_id=teacher.id,
        target_label=teacher.email,
        school_id=school_id,
        detail="Pending invitation withdrawn and the account removed.",
    )
    await db.delete(teacher)
    await db.commit()


# ── Direct credential management ─────────────────────────────────────────────
#
# The invite flow assumes a reachable inbox: the account is created without a
# password and only becomes usable once the emailed link is clicked. That falls
# apart when email is unconfigured on the deployment, the address bounces, or
# the admin is onboarding someone sitting next to them. These endpoints let the
# super admin set a password and activate an account directly, so nobody stays
# stranded behind an email that never arrives.


async def _set_user_password(
    db: AsyncSession,
    *,
    user: SchoolAdmin | Teacher,
    user_type: str,
    admin,
    body: SetPasswordRequest,
) -> CredentialUpdateOut:
    if not verify_password(body.admin_password, admin.hashed_password):
        raise HTTPException(status_code=403, detail="Incorrect password")

    was_active = user.status == "active"
    user.hashed_password = hash_password(body.new_password)

    if body.activate:
        user.status = "active"
        user.rejected_reason = None
        # A password handed over by the super admin is at least as strong a
        # proof of ownership as clicking a link in the inbox would have been.
        user.email_verified = True
        if user.approved_at is None:
            user.approved_at = datetime.utcnow()
            if user_type == "school_admin":
                user.approved_by_admin_id = admin.id

    # An invite, verification or reset link still sitting in an inbox would let
    # whoever holds it overwrite the password that was just set, so burn them.
    await db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_type == user_type,
            AuthToken.user_id == user.id,
            AuthToken.purpose.in_(("invite", "email_verification", "password_reset")),
            AuthToken.used_at.is_(None),
        )
        .values(used_at=datetime.utcnow())
    )
    # The single highest-value thing in this file to have a record of: setting
    # someone else's password leaves a hash indistinguishable from one they
    # chose themselves, so afterwards there is nothing in the data to show it
    # happened, who did it, or when.
    audit_service.record(
        db,
        actor=admin,
        action=f"{user_type}.set_password",
        target_type=user_type,
        target_id=user.id,
        target_label=user.email,
        school_id=getattr(user, "school_id", None),
        detail=(
            "Password set directly by a super admin; any outstanding invite and "
            "verification links were invalidated."
            + (" Account activated." if body.activate and not was_active else "")
        ),
    )
    await db.commit()
    await db.refresh(user)

    activated = body.activate and not was_active
    return CredentialUpdateOut(
        user_type=user_type,
        user_id=user.id,
        name=user.name,
        email=user.email,
        status=user.status,
        activated=activated,
        detail=(
            f"Password set for {user.email}."
            + (" The account is now active." if activated else "")
        ),
    )


@router.post("/schools/{school_id}/admin/set-password", response_model=CredentialUpdateOut)
async def set_school_admin_password(
    school_id: int,
    body: SetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    """Set or reset a school admin's password without sending any email."""
    school_admin = await _get_school_admin_for_school(school_id, db)
    return await _set_user_password(
        db, user=school_admin, user_type="school_admin", admin=admin, body=body
    )


@router.post(
    "/schools/{school_id}/teachers/{teacher_id}/set-password",
    response_model=CredentialUpdateOut,
)
async def set_teacher_password(
    school_id: int,
    teacher_id: int,
    body: SetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    """Set or reset a teacher's password without sending any email."""
    teacher = await _get_school_teacher(teacher_id, school_id, db)
    return await _set_user_password(
        db, user=teacher, user_type="teacher", admin=admin, body=body
    )


@router.post("/schools/{school_id}/admin/activate", response_model=SchoolOut)
async def activate_school_admin(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    """Activate a school admin from any state, bypassing the emailed invite.

    Unlike approve/reactivate this does not care which state the account starts
    in, which is the point: a rejected or stalled admin can be waved through
    once the super admin has verified them out of band.
    """
    school_admin = await _get_school_admin_for_school(school_id, db)
    _require_password_set(school_admin, "school admin")

    if school_admin.status != "active":
        school_admin.status = "active"
        school_admin.rejected_reason = None
        if school_admin.approved_at is None:
            school_admin.approved_at = datetime.utcnow()
            school_admin.approved_by_admin_id = admin.id
    school_admin.email_verified = True
    audit_service.record(
        db,
        actor=admin,
        action="school_admin.activate",
        target_type="school_admin",
        target_id=school_admin.id,
        target_label=school_admin.email,
        school_id=school_id,
        detail="Activated directly, bypassing the emailed invite.",
    )
    await db.commit()
    return await _get_school_with_admin(school_id, db)


@router.post(
    "/schools/{school_id}/teachers/{teacher_id}/activate", response_model=TeacherOut
)
async def activate_teacher(
    school_id: int,
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    """Activate a teacher from any state, bypassing the emailed invite."""
    teacher = await _get_school_teacher(teacher_id, school_id, db)
    _require_password_set(teacher, "teacher")

    if teacher.status != "active":
        teacher.status = "active"
        teacher.rejected_reason = None
        if teacher.approved_at is None:
            teacher.approved_at = datetime.utcnow()
    teacher.email_verified = True
    audit_service.record(
        db,
        actor=admin,
        action="teacher.activate",
        target_type="teacher",
        target_id=teacher.id,
        target_label=teacher.email,
        school_id=school_id,
        detail="Activated directly, bypassing the emailed invite.",
    )
    await db.commit()

    result = await db.execute(
        select(Teacher).options(selectinload(Teacher.school)).where(Teacher.id == teacher.id)
    )
    return _teacher_out(result.scalar_one())


def _require_password_set(user: SchoolAdmin | Teacher, role_label: str) -> None:
    """Activating an account with no password would create a dead end.

    It cannot be logged into, and once active it no longer looks like it is
    waiting for anything, so the problem stops being visible from the UI.
    """
    if not user.hashed_password:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This {role_label} has no password yet. Set one first, or "
                "resend their invitation."
            ),
        )


async def _get_school_teacher(teacher_id: int, school_id: int, db: AsyncSession) -> Teacher:
    result = await db.execute(
        select(Teacher).where(Teacher.id == teacher_id, Teacher.school_id == school_id)
    )
    teacher = result.scalar_one_or_none()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found in this organisation")
    return teacher


def _teacher_out(t: Teacher) -> TeacherOut:
    return TeacherOut(
        id=t.id,
        name=t.name,
        email=t.email,
        profile_image_url=t.profile_image_url,
        school_id=t.school_id,
        school_name=t.school.name if t.school else None,
        created_at=t.created_at,
        status=t.status,
        email_verified=t.email_verified,
        rejected_reason=t.rejected_reason,
    )


@router.get("/schools/{school_id}/classes", response_model=list[ClassOut])
async def list_school_classes(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    result = await db.execute(
        select(Class).where(Class.school_id == school_id).order_by(
    Class.name,
    Class.section,
)
    )
    return result.scalars().all()


@router.get("/schools/{school_id}/classes/{class_id}", response_model=ClassOut)
async def get_school_class(
    school_id: int,
    class_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    class_ = await _get_school_class(class_id, school_id, db)
    return class_


@router.get("/schools/{school_id}/classes/{class_id}/subjects", response_model=list[SubjectOut])
async def list_school_class_subjects(
    school_id: int,
    class_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    await _get_school_class(class_id, school_id, db)
    result = await db.execute(
        select(Subject)
        .options(selectinload(Subject.teacher))
        .where(Subject.class_id == class_id)
        .order_by(Subject.name)
    )
    return result.scalars().all()


@router.get("/schools/{school_id}/subjects/{subject_id}/students", response_model=list[StudentOut])
async def list_school_subject_students(
    school_id: int,
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    subject = await _get_school_subject(subject_id, school_id, db)
    result = await db.execute(
        select(Student)
        .where(Student.class_id == subject.class_id)
        .order_by(Student.name)
    )
    return result.scalars().all()


@router.get("/schools/{school_id}/subjects/{subject_id}/recordings", response_model=list[RecordingWithReport])
async def list_school_subject_recordings(
    school_id: int,
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    await _get_school_subject(subject_id, school_id, db)
    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.report).selectinload(LLMReport.recording),
            selectinload(Recording.learning_plan),
        )
        .where(Recording.subject_id == subject_id)
        .order_by(Recording.uploaded_at.desc())
    )
    return result.scalars().all()


@router.delete("/recordings/{recording_id}", status_code=204)
async def delete_recording(
    recording_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_admin),
):
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    await db.execute(delete(LLMReport).where(LLMReport.recording_id == recording_id))
    audit_service.record(
        db,
        actor=admin,
        action="recording.delete",
        target_type="recording",
        target_id=recording.id,
        target_label=recording.lecture_title or recording.chapter_name,
        detail="Recording and its analysis report deleted.",
    )
    await db.delete(recording)
    await db.commit()


async def _get_school_class(class_id: int, school_id: int, db: AsyncSession) -> Class:
    result = await db.execute(
        select(Class).where(Class.id == class_id, Class.school_id == school_id)
    )
    class_ = result.scalar_one_or_none()
    if not class_:
        raise HTTPException(status_code=404, detail="Class not found")
    return class_


async def _get_school_subject(subject_id: int, school_id: int, db: AsyncSession) -> Subject:
    result = await db.execute(
        select(Subject)
        .join(Class, Subject.class_id == Class.id)
        .where(Subject.id == subject_id, Class.school_id == school_id)
    )
    subject = result.scalar_one_or_none()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    return subject
