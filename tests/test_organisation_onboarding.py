"""Super-admin organisation onboarding: create an org, invite staff, accept.

These exercise the whole loop the super admin actually performs, because the
individual pieces (token minting, email queuing, accept-invite) were each fine
in isolation while the joined-up flow was not reachable.
"""

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.auth_token import AuthToken
from app.models.teacher import Teacher


@pytest.fixture(autouse=True)
def capture_emails(monkeypatch):
    """Intercept delivery so tests can read the invite links that were sent."""
    sent: list[dict] = []

    def fake_send(to_email, subject, html_body, to_name=None):
        sent.append(
            {"to": to_email, "subject": subject, "html": html_body, "name": to_name}
        )

    monkeypatch.setattr("app.services.email_service.send_email", fake_send)
    return sent


def _link_token(html: str) -> str:
    """Pull the raw invite token out of the emailed link."""
    marker = "accept-invite?token="
    start = html.index(marker) + len(marker)
    end = start
    while end < len(html) and html[end] not in "'\"< ":
        end += 1
    return html[start:end]


async def _teachers_by_email():
    """Read teachers on the test's own event loop.

    The shared `db_session` fixture is bound to the session-scoped loop, so
    touching it after an HTTP call raises "attached to a different loop".
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Teacher).order_by(Teacher.email))
        return list(result.scalars().all())


async def _auth_tokens_for(user_type: str, user_id: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AuthToken).where(
                AuthToken.user_type == user_type, AuthToken.user_id == user_id
            )
        )
        return list(result.scalars().all())


async def test_create_organisation_invites_admin_and_teachers(
    client, admin_token, capture_emails
):
    response = await client.post(
        "/admin/schools",
        headers={"Authorization": admin_token},
        json={
            "name": "Oakridge Academy",
            "address": "12 Hill Road",
            "admin_name": "Elena Rodriguez",
            "admin_email": "elena@oakridge.edu",
            "teachers": [
                {"name": "Priya Sharma", "email": "priya@oakridge.edu"},
                {"name": "Sam Bell", "email": "sam@oakridge.edu"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["admin"]["email"] == "elena@oakridge.edu"
    assert body["admin"]["status"] == "invited"

    teachers = await _teachers_by_email()
    assert [t.email for t in teachers] == ["priya@oakridge.edu", "sam@oakridge.edu"]
    assert all(t.status == "invited" for t in teachers)
    assert all(t.hashed_password is None for t in teachers)
    assert all(t.school_id == body["id"] for t in teachers)

    # One email per invited person, each carrying its own link.
    recipients = sorted(m["to"] for m in capture_emails)
    assert recipients == ["elena@oakridge.edu", "priya@oakridge.edu", "sam@oakridge.edu"]
    tokens = {_link_token(m["html"]) for m in capture_emails}
    assert len(tokens) == 3, "each invitee must get a distinct token"


async def test_invited_teacher_accepts_and_becomes_active(
    client, admin_token, capture_emails
):
    await client.post(
        "/admin/schools",
        headers={"Authorization": admin_token},
        json={
            "name": "Oakridge Academy",
            "admin_name": "Elena Rodriguez",
            "admin_email": "elena@oakridge.edu",
            "teachers": [{"name": "Priya Sharma", "email": "priya@oakridge.edu"}],
        },
    )
    teacher_mail = next(m for m in capture_emails if m["to"] == "priya@oakridge.edu")

    accept = await client.post(
        "/auth/accept-invite",
        json={"token": _link_token(teacher_mail["html"]), "password": "s3cret-pass"},
    )
    assert accept.status_code == 200, accept.text
    assert accept.json()["role"] == "teacher"

    teacher = (await _teachers_by_email())[0]
    # A super-admin invite is trusted: no school-admin approval step.
    assert teacher.status == "active"
    assert teacher.email_verified is True

    login = await client.post(
        "/auth/login",
        data={"username": "priya@oakridge.edu", "password": "s3cret-pass"},
    )
    assert login.status_code == 200, login.text


async def test_bulk_invite_reports_skips_without_aborting_the_batch(
    client, admin_token, capture_emails, school, teacher_user
):
    """teacher_user already occupies teacher@test.com in this school."""
    response = await client.post(
        f"/admin/schools/{school.id}/teachers/invite",
        headers={"Authorization": admin_token},
        json={
            "teachers": [
                {"name": "Fresh Face", "email": "fresh@test.com"},
                {"name": "Already There", "email": "teacher@test.com"},
                {"name": "Dupe", "email": "fresh@test.com"},
            ]
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["invited_count"] == 1
    assert body["skipped_count"] == 2
    # Keyed by position, not email: the duplicate row shares an address with
    # the first, so a dict keyed on email would hide one of the two outcomes.
    assert [(r["email"], r["outcome"]) for r in body["results"]] == [
        ("fresh@test.com", "invited"),
        ("teacher@test.com", "skipped"),
        ("fresh@test.com", "skipped"),
    ]
    assert body["results"][2]["reason"] == "Duplicate address in this request"

    # Only the one genuinely new teacher was mailed.
    assert [m["to"] for m in capture_emails] == ["fresh@test.com"]


async def test_resending_an_invite_invalidates_the_previous_link(
    client, admin_token, capture_emails
):
    created = await client.post(
        "/admin/schools",
        headers={"Authorization": admin_token},
        json={
            "name": "Oakridge Academy",
            "admin_name": "Elena Rodriguez",
            "admin_email": "elena@oakridge.edu",
        },
    )
    school_id = created.json()["id"]
    first_token = _link_token(capture_emails[0]["html"])

    resend = await client.post(
        f"/admin/schools/{school_id}/admin/resend-invite",
        headers={"Authorization": admin_token},
    )
    assert resend.status_code == 202, resend.text
    second_token = _link_token(capture_emails[-1]["html"])
    assert second_token != first_token

    # The superseded link must no longer work, or a resend could not revoke a
    # mis-sent invitation.
    stale = await client.post(
        "/auth/accept-invite", json={"token": first_token, "password": "s3cret-pass"}
    )
    assert stale.status_code == 400

    fresh = await client.post(
        "/auth/accept-invite", json={"token": second_token, "password": "s3cret-pass"}
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["role"] == "school_admin"


async def test_resend_rejected_once_the_invite_was_accepted(
    client, admin_token, capture_emails
):
    created = await client.post(
        "/admin/schools",
        headers={"Authorization": admin_token},
        json={
            "name": "Oakridge Academy",
            "admin_name": "Elena Rodriguez",
            "admin_email": "elena@oakridge.edu",
        },
    )
    school_id = created.json()["id"]
    await client.post(
        "/auth/accept-invite",
        json={"token": _link_token(capture_emails[0]["html"]), "password": "s3cret-pass"},
    )

    resend = await client.post(
        f"/admin/schools/{school_id}/admin/resend-invite",
        headers={"Authorization": admin_token},
    )
    assert resend.status_code == 409


async def test_revoking_a_pending_invite_removes_account_and_tokens(
    client, admin_token, capture_emails
):
    created = await client.post(
        "/admin/schools",
        headers={"Authorization": admin_token},
        json={
            "name": "Oakridge Academy",
            "admin_name": "Elena Rodriguez",
            "admin_email": "elena@oakridge.edu",
            "teachers": [{"name": "Priya Sharma", "email": "priya@oakridge.edu"}],
        },
    )
    school_id = created.json()["id"]
    teacher = (await _teachers_by_email())[0]
    teacher_mail = next(m for m in capture_emails if m["to"] == "priya@oakridge.edu")
    revoked_token = _link_token(teacher_mail["html"])

    response = await client.delete(
        f"/admin/schools/{school_id}/teachers/{teacher.id}",
        headers={"Authorization": admin_token},
    )
    assert response.status_code == 204, response.text

    assert await _teachers_by_email() == []
    assert await _auth_tokens_for("teacher", teacher.id) == []

    # The already-delivered link must be dead, not merely orphaned.
    stale = await client.post(
        "/auth/accept-invite", json={"token": revoked_token, "password": "s3cret-pass"}
    )
    assert stale.status_code == 400


async def test_revoking_an_active_teacher_is_refused(
    client, admin_token, school, teacher_user
):
    response = await client.delete(
        f"/admin/schools/{school.id}/teachers/{teacher_user.id}",
        headers={"Authorization": admin_token},
    )
    assert response.status_code == 409


async def test_listing_organisation_teachers(client, admin_token, school, teacher_user):
    response = await client.get(
        f"/admin/schools/{school.id}/teachers",
        headers={"Authorization": admin_token},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [t["email"] for t in body] == ["teacher@test.com"]
    assert body[0]["school_name"] == school.name


async def test_teacher_endpoints_require_super_admin(client, school, school_admin_token):
    response = await client.get(
        f"/admin/schools/{school.id}/teachers",
        headers={"Authorization": school_admin_token},
    )
    assert response.status_code == 403
