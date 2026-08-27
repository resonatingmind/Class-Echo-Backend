"""Super admin setting passwords and activating accounts directly.

The invite flow assumes email works. These cover the escape hatch for when it
does not: the super admin hands over a password themselves, and the account has
to end up genuinely usable — logged into, with any live invite link dead.
"""

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.auth_token import AuthToken
from app.models.school_admin import SchoolAdmin
from app.models.teacher import Teacher


@pytest.fixture(autouse=True)
def capture_emails(monkeypatch):
    sent: list[dict] = []

    def fake_send(to_email, subject, html_body, to_name=None):
        sent.append({"to": to_email, "subject": subject, "html": html_body})

    monkeypatch.setattr("app.services.email_service.send_email", fake_send)
    return sent


def _link_token(html: str) -> str:
    marker = "accept-invite?token="
    start = html.index(marker) + len(marker)
    end = start
    while end < len(html) and html[end] not in "'\"< ":
        end += 1
    return html[start:end]


async def _teacher_by_email(email: str) -> Teacher:
    """Read on the test's own event loop; db_session belongs to another one."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Teacher).where(Teacher.email == email))
        return result.scalar_one()


async def _school_admin_by_email(email: str) -> SchoolAdmin:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(SchoolAdmin).where(SchoolAdmin.email == email)
        )
        return result.scalar_one()


async def _create_org(client, admin_token, **teachers):
    response = await client.post(
        "/admin/schools",
        headers={"Authorization": admin_token},
        json={
            "name": "Oakridge Academy",
            "admin_name": "Elena Rodriguez",
            "admin_email": "elena@oakridge.edu",
            "teachers": [{"name": n, "email": e} for n, e in teachers.items()],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_set_password_activates_an_invited_school_admin(
    client, admin_token, capture_emails
):
    school_id = await _create_org(client, admin_token)
    capture_emails.clear()

    response = await client.post(
        f"/admin/schools/{school_id}/admin/set-password",
        headers={"Authorization": admin_token},
        json={"new_password": "handed-over-1", "admin_password": "testpass"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "active"
    assert body["activated"] is True

    # The whole point is that nothing had to be delivered.
    assert capture_emails == []

    login = await client.post(
        "/auth/login",
        data={"username": "elena@oakridge.edu", "password": "handed-over-1"},
    )
    assert login.status_code == 200, login.text
    assert login.json()["role"] == "school_admin"


async def test_set_password_kills_the_outstanding_invite_link(
    client, admin_token, capture_emails
):
    """Otherwise whoever holds the emailed link could overwrite the new password."""
    school_id = await _create_org(client, admin_token)
    invite_token = _link_token(capture_emails[0]["html"])

    await client.post(
        f"/admin/schools/{school_id}/admin/set-password",
        headers={"Authorization": admin_token},
        json={"new_password": "handed-over-1", "admin_password": "testpass"},
    )

    stale = await client.post(
        "/auth/accept-invite",
        json={"token": invite_token, "password": "attacker-chosen"},
    )
    assert stale.status_code == 400

    login = await client.post(
        "/auth/login",
        data={"username": "elena@oakridge.edu", "password": "handed-over-1"},
    )
    assert login.status_code == 200, login.text


async def test_set_password_requires_the_admins_own_password(
    client, admin_token, school, school_admin_user
):
    response = await client.post(
        f"/admin/schools/{school.id}/admin/set-password",
        headers={"Authorization": admin_token},
        json={"new_password": "handed-over-1", "admin_password": "not-my-password"},
    )
    assert response.status_code == 403

    # The old password must still be the only one that works.
    unchanged = await client.post(
        "/auth/login",
        data={"username": "schooladmin@test.com", "password": "testpass"},
    )
    assert unchanged.status_code == 200, unchanged.text


async def test_reset_teacher_password_without_activating_leaves_status_alone(
    client, admin_token, school, teacher_user, db_session
):
    teacher_user.status = "suspended"
    await db_session.commit()

    response = await client.post(
        f"/admin/schools/{school.id}/teachers/{teacher_user.id}/set-password",
        headers={"Authorization": admin_token},
        json={
            "new_password": "handed-over-1",
            "admin_password": "testpass",
            "activate": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "suspended"
    assert response.json()["activated"] is False

    teacher = await _teacher_by_email("teacher@test.com")
    assert teacher.status == "suspended"


async def test_set_password_rejects_short_passwords(
    client, admin_token, school, teacher_user
):
    response = await client.post(
        f"/admin/schools/{school.id}/teachers/{teacher_user.id}/set-password",
        headers={"Authorization": admin_token},
        json={"new_password": "short", "admin_password": "testpass"},
    )
    assert response.status_code == 422


async def test_activate_teacher_skips_the_approval_step(
    client, admin_token, school, teacher_user, db_session, capture_emails
):
    teacher_user.status = "pending_approval"
    await db_session.commit()

    response = await client.post(
        f"/admin/schools/{school.id}/teachers/{teacher_user.id}/activate",
        headers={"Authorization": admin_token},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    assert capture_emails == []

    login = await client.post(
        "/auth/login", data={"username": "teacher@test.com", "password": "testpass"}
    )
    assert login.status_code == 200, login.text


async def test_activating_an_invited_teacher_is_refused(client, admin_token):
    """They have no password, so activation alone would strand the account."""
    school_id = await _create_org(client, admin_token, **{"Priya Sharma": "priya@oakridge.edu"})
    teacher = await _teacher_by_email("priya@oakridge.edu")

    response = await client.post(
        f"/admin/schools/{school_id}/teachers/{teacher.id}/activate",
        headers={"Authorization": admin_token},
    )
    assert response.status_code == 409
    assert "no password" in response.json()["detail"]


async def test_set_password_scoped_to_the_named_organisation(
    client, admin_token, school, teacher_user
):
    other_school_id = await _create_org(client, admin_token)

    response = await client.post(
        f"/admin/schools/{other_school_id}/teachers/{teacher_user.id}/set-password",
        headers={"Authorization": admin_token},
        json={"new_password": "handed-over-1", "admin_password": "testpass"},
    )
    assert response.status_code == 404


async def test_only_the_super_admin_may_set_passwords(
    client, school_admin_token, teacher_token, school, teacher_user
):
    for token in (school_admin_token, teacher_token):
        response = await client.post(
            f"/admin/schools/{school.id}/teachers/{teacher_user.id}/set-password",
            headers={"Authorization": token},
            json={"new_password": "handed-over-1", "admin_password": "testpass"},
        )
        assert response.status_code == 403


async def test_activate_school_admin_recovers_a_rejected_registration(
    client, admin_token, school, school_admin_user, db_session
):
    school_admin_user.status = "rejected"
    school_admin_user.rejected_reason = "Unverified domain"
    await db_session.commit()

    response = await client.post(
        f"/admin/schools/{school.id}/admin/activate",
        headers={"Authorization": admin_token},
    )
    assert response.status_code == 200, response.text
    assert response.json()["admin"]["status"] == "active"

    stored = await _school_admin_by_email("schooladmin@test.com")
    assert stored.rejected_reason is None
