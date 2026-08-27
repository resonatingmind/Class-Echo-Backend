from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, update
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.core.deps import get_admin
from app.core.security import hash_password, verify_password
from app.config import settings
from app.models.auth_token import AuthToken
from app.models.school import School
from app.models.school_admin import SchoolAdmin
from app.models.teacher import Teacher
from app.services import email_service, invite_service
from app.services import teacher_onboarding_service as onboarding
from app.models.class_ import Class
from app.models.subject import Subject
from app.models.student import Student
from app.models.recording import Recording
from app.models.llm_report import LLMReport
from app.schemas.school import (
    SchoolCreate,
    SchoolUpdate,
    SchoolOut,
    SchoolRejectRequest,
    SchoolDeleteRequest,
)
from app.schemas.admin import (
    AdminProfileOut,
    CredentialUpdateOut,
    EmailStatusOut,
    EmailTestRequest,
    EmailTestResult,
    SetPasswordRequest,
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
            "the server log and never delivered. Set MAILJET_API_KEY, "
            "MAILJET_API_SECRET and MAILJET_FROM_EMAIL on this deployment."
        )
    if backend == "mailjet" and settings.MAILJET_SANDBOX:
        warnings.append(
            "MAILJET_SANDBOX is enabled: Mailjet validates messages but "
            "delivers nothing. Correct for staging, wrong for production."
        )
    if "localhost" in settings.FRONTEND_URL or "127.0.0.1" in settings.FRONTEND_URL:
        warnings.append(
            f"FRONTEND_URL is {settings.FRONTEND_URL!r}, so emailed invite links "
            "point at the server's own machine and will not open for recipients. "
            "Set it to the public URL of the app."
        )

    return EmailStatusOut(
        backend=backend,
        delivers_email=backend != "log",
        from_email=(
            settings.MAILJET_FROM_EMAIL if backend == "mailjet" else settings.SMTP_FROM_EMAIL
        ),
        from_name=settings.MAILJET_FROM_NAME if backend == "mailjet" else None,
        sandbox=settings.MAILJET_SANDBOX,
        frontend_url=settings.FRONTEND_URL,
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
                "This deployment has no MAILJET_* or SMTP_* variables set, so "
                "every message is written to the server log and nothing is "
                "delivered. Nothing was sent."
            ),
        )

    from_email = (
        settings.MAILJET_FROM_EMAIL if backend == "mailjet" else settings.SMTP_FROM_EMAIL
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
        if "API key" in message or "key/secret" in message:
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
        # clean success here still means no email arrives.
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
    )
    for teacher, token in teacher_invites:
        invite_service.queue_invite_email(
            to_email=teacher.email,
            to_name=teacher.name,
            organisation_name=school.name,
            role_label="teacher",
            raw_token=token,
            inviter_name=admin.name,
        )

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

    class_ids = select(Class.id).where(Class.school_id == school_id)
    subject_ids = select(Subject.id).where(Subject.class_id.in_(class_ids))
    recording_ids = select(Recording.id).where(Recording.subject_id.in_(subject_ids))

    await db.execute(
        delete(LLMReport).where(LLMReport.recording_id.in_(recording_ids))
    )
    await db.execute(
        delete(Recording).where(Recording.id.in_(recording_ids))
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
    await db.execute(
        delete(SchoolAdmin).where(SchoolAdmin.school_id == school_id)
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
    await db.commit()
    return await _get_school_with_admin(school_id, db)


@router.post("/schools/{school_id}/reject", response_model=SchoolOut)
async def reject_school(
    school_id: int,
    body: SchoolRejectRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    school_admin = await _get_school_admin_for_school(school_id, db)
    if school_admin.status != "pending_approval":
        raise HTTPException(status_code=400, detail="Only pending schools can be rejected")

    school_admin.status = "rejected"
    school_admin.rejected_reason = body.reason
    await db.commit()
    return await _get_school_with_admin(school_id, db)


@router.post("/schools/{school_id}/suspend", response_model=SchoolOut)
async def suspend_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    school_admin = await _get_school_admin_for_school(school_id, db)
    if school_admin.status != "active":
        raise HTTPException(status_code=400, detail="Only active schools can be suspended")

    school_admin.status = "suspended"
    await db.commit()
    return await _get_school_with_admin(school_id, db)


@router.post("/schools/{school_id}/reactivate", response_model=SchoolOut)
async def reactivate_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
):
    school_admin = await _get_school_admin_for_school(school_id, db)
    if school_admin.status != "suspended":
        raise HTTPException(status_code=400, detail="Only suspended schools can be reactivated")

    school_admin.status = "active"
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
        )

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
    )
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
    )
    return {"detail": f"Invitation resent to {teacher.email}"}


@router.delete("/schools/{school_id}/teachers/{teacher_id}", status_code=204)
async def revoke_teacher_invite(
    school_id: int,
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
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

    # An invite or verification link still sitting in an inbox would let whoever
    # holds it overwrite the password that was just set, so burn them.
    await db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_type == user_type,
            AuthToken.user_id == user.id,
            AuthToken.purpose.in_(("invite", "email_verification")),
            AuthToken.used_at.is_(None),
        )
        .values(used_at=datetime.utcnow())
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
    await db.commit()
    return await _get_school_with_admin(school_id, db)


@router.post(
    "/schools/{school_id}/teachers/{teacher_id}/activate", response_model=TeacherOut
)
async def activate_teacher(
    school_id: int,
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_admin),
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
    _=Depends(get_admin),
):
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    await db.execute(delete(LLMReport).where(LLMReport.recording_id == recording_id))
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
