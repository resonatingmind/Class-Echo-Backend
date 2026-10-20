"""Self-service password reset: POST /auth/forgot-password and /auth/reset-password.

A user who forgot their password has to be able to get back in without an
admin, and the endpoint must not reveal which emails have accounts.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.core import rate_limit
from app.core.security import generate_token_pair
from app.database import AsyncSessionLocal
from app.models.auth_token import AuthToken
from app.models.teacher import Teacher


@pytest.fixture(autouse=True)
def capture_emails(monkeypatch):
    sent: list[dict] = []

    def fake_send(to_email, subject, html_body, to_name=None):
        sent.append({"to": to_email, "subject": subject, "html": html_body})

    monkeypatch.setattr("app.services.email_service.send_email", fake_send)
    return sent


@pytest.fixture(autouse=True)
def fresh_rate_limit(monkeypatch):
    # Redis counters outlive clean_db (and whole test runs), so every test gets
    # an empty in-memory limiter instead.
    monkeypatch.setattr(rate_limit, "_get_client", lambda: None)
    monkeypatch.setattr(rate_limit, "_memory", {})


def _link_token(html: str) -> str:
    marker = "reset-password?token="
    start = html.index(marker) + len(marker)
    end = start
    while end < len(html) and html[end] not in "'\"< ":
        end += 1
    return html[start:end]


async def _request_reset(client, capture_emails, email: str) -> str:
    resp = await client.post("/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200
    return _link_token(capture_emails[-1]["html"])


@pytest.mark.parametrize(
    "fixture_name, role",
    [
        ("admin_user", "admin"),
        ("school_admin_user", "school_admin"),
        ("teacher_user", "teacher"),
        ("student_user", "student"),
    ],
)
async def test_every_role_can_reset_and_log_in(
    client, capture_emails, request, fixture_name, role
):
    user = request.getfixturevalue(fixture_name)
    token = await _request_reset(client, capture_emails, user.email)
    assert capture_emails[-1]["to"] == user.email

    resp = await client.post(
        "/auth/reset-password", json={"token": token, "password": "brandnew123"}
    )
    assert resp.status_code == 200

    old = await client.post(
        "/auth/login", json={"email": user.email, "password": "testpass"}
    )
    assert old.status_code == 401
    new = await client.post(
        "/auth/login", json={"email": user.email, "password": "brandnew123"}
    )
    assert new.status_code == 200
    assert new.json()["role"] == role


async def test_unknown_email_gets_same_response_and_no_email(client, capture_emails, teacher_user):
    known = await client.post("/auth/forgot-password", json={"email": teacher_user.email})
    capture_emails.clear()
    unknown = await client.post("/auth/forgot-password", json={"email": "nobody@nowhere.com"})

    assert unknown.status_code == 200
    assert unknown.json() == known.json()
    assert capture_emails == []


async def test_reset_link_works_only_once(client, capture_emails, teacher_user):
    token = await _request_reset(client, capture_emails, teacher_user.email)
    first = await client.post(
        "/auth/reset-password", json={"token": token, "password": "brandnew123"}
    )
    assert first.status_code == 200

    again = await client.post(
        "/auth/reset-password", json={"token": token, "password": "another123"}
    )
    assert again.status_code == 400


async def test_requesting_again_kills_the_earlier_link(client, capture_emails, teacher_user):
    first = await _request_reset(client, capture_emails, teacher_user.email)
    second = await _request_reset(client, capture_emails, teacher_user.email)

    stale = await client.post(
        "/auth/reset-password", json={"token": first, "password": "brandnew123"}
    )
    assert stale.status_code == 400
    fresh = await client.post(
        "/auth/reset-password", json={"token": second, "password": "brandnew123"}
    )
    assert fresh.status_code == 200


async def test_expired_link_is_rejected(client, teacher_user):
    raw_token, token_hash = generate_token_pair()
    async with AsyncSessionLocal() as session:
        session.add(
            AuthToken(
                user_type="teacher",
                user_id=teacher_user.id,
                token_hash=token_hash,
                purpose="password_reset",
                expires_at=datetime.utcnow() - timedelta(minutes=1),
            )
        )
        await session.commit()

    resp = await client.post(
        "/auth/reset-password", json={"token": raw_token, "password": "brandnew123"}
    )
    assert resp.status_code == 400


async def test_invite_token_cannot_be_used_as_reset_token(client, teacher_user):
    raw_token, token_hash = generate_token_pair()
    async with AsyncSessionLocal() as session:
        session.add(
            AuthToken(
                user_type="teacher",
                user_id=teacher_user.id,
                token_hash=token_hash,
                purpose="invite",
                expires_at=datetime.utcnow() + timedelta(days=1),
            )
        )
        await session.commit()

    resp = await client.post(
        "/auth/reset-password", json={"token": raw_token, "password": "brandnew123"}
    )
    assert resp.status_code == 400


async def test_short_password_is_rejected(client, capture_emails, teacher_user):
    token = await _request_reset(client, capture_emails, teacher_user.email)
    resp = await client.post(
        "/auth/reset-password", json={"token": token, "password": "short"}
    )
    assert resp.status_code == 422


async def test_invited_account_without_password_gets_no_reset_email(
    client, capture_emails, school
):
    async with AsyncSessionLocal() as session:
        session.add(
            Teacher(
                name="Invited",
                email="invited@test.com",
                hashed_password=None,
                school_id=school.id,
                status="invited",
            )
        )
        await session.commit()

    resp = await client.post("/auth/forgot-password", json={"email": "invited@test.com"})
    assert resp.status_code == 200
    assert capture_emails == []

    async with AsyncSessionLocal() as session:
        tokens = (
            await session.execute(
                select(AuthToken).where(AuthToken.purpose == "password_reset")
            )
        ).scalars().all()
    assert tokens == []


# ── Rate limiting: PASSWORD_RESET_LIMIT requests per (email, IP) ──────────────


async def _forgot(client, email: str, ip: str | None = None):
    headers = {"X-Forwarded-For": ip} if ip else {}
    return await client.post("/auth/forgot-password", json={"email": email}, headers=headers)


async def test_limit_blocks_after_ten_requests(client, capture_emails, teacher_user):
    limit = settings.PASSWORD_RESET_LIMIT
    for _ in range(limit):
        assert (await _forgot(client, teacher_user.email)).status_code == 200
    assert len(capture_emails) == limit

    blocked = await _forgot(client, teacher_user.email)
    assert blocked.status_code == 429
    assert len(capture_emails) == limit


async def test_limit_is_per_ip(client, teacher_user):
    for _ in range(settings.PASSWORD_RESET_LIMIT):
        await _forgot(client, teacher_user.email, ip="203.0.113.1")
    assert (await _forgot(client, teacher_user.email, ip="203.0.113.1")).status_code == 429
    assert (await _forgot(client, teacher_user.email, ip="203.0.113.2")).status_code == 200


async def test_limit_is_per_email(client, teacher_user, student_user):
    for _ in range(settings.PASSWORD_RESET_LIMIT):
        await _forgot(client, teacher_user.email)
    assert (await _forgot(client, teacher_user.email)).status_code == 429
    assert (await _forgot(client, student_user.email)).status_code == 200


async def test_email_case_does_not_reset_the_counter(client, teacher_user):
    for _ in range(settings.PASSWORD_RESET_LIMIT):
        await _forgot(client, teacher_user.email)
    assert (await _forgot(client, teacher_user.email.upper())).status_code == 429


async def test_unknown_email_is_limited_the_same_way(client):
    for _ in range(settings.PASSWORD_RESET_LIMIT):
        assert (await _forgot(client, "nobody@nowhere.com")).status_code == 200
    assert (await _forgot(client, "nobody@nowhere.com")).status_code == 429


async def test_spoofed_forwarded_for_does_not_bypass_limit(client, teacher_user):
    # The proxy appends the real address last; anything a client sends sits to
    # its left and must be ignored.
    for i in range(settings.PASSWORD_RESET_LIMIT):
        await _forgot(client, teacher_user.email, ip=f"10.0.0.{i}, 198.51.100.7")
    blocked = await _forgot(client, teacher_user.email, ip="10.9.9.9, 198.51.100.7")
    assert blocked.status_code == 429
