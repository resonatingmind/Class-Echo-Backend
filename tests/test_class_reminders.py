"""
Tests for brief open tracking, device tokens and the class reminder push.

What these tests cover:
- A brief open is counted once per class per day, and feeds the super admin's brief open rate
- Device tokens are registered with a timezone, move to whoever signs in, and can be removed
- Reminders go out only for classes starting in about 10 minutes in the teacher's own timezone
- Muted classes get no reminder; a class is reminded once per day
- A token Expo reports as no longer registered is dropped
"""
from datetime import date, datetime, time, timezone

import pytest_asyncio
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.class_brief_mute import ClassBriefMute
from app.models.device_token import DeviceToken
from app.models.llm_report import LLMReport
from app.models.recording import Recording, RecordingStatus
from app.models.timetable_entry import TimetableEntry
from app.services.class_reminder_service import send_due_reminders

# Thursday 29 Oct 2026, 03:20 UTC = 08:50 in Asia/Kolkata.
NOW = datetime(2026, 10, 29, 3, 20, tzinfo=timezone.utc)
TOKEN = "ExponentPushToken[abcdefghijklmnop]"


async def _add(*rows):
    async with AsyncSessionLocal() as session:
        session.add_all(rows)
        await session.commit()
        for row in rows:
            await session.refresh(row)
    return rows[0]


@pytest_asyncio.fixture
async def period(school, class_, subject, teacher_user):
    return await _add(TimetableEntry(
        school_id=school.id, class_id=class_.id, subject_id=subject.id, teacher_id=teacher_user.id,
        day_of_week="Thursday", period_number=1, start_time=time(9, 0), end_time=time(9, 40),
    ))


def _sync_session():
    engine = create_engine(settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"))
    return sessionmaker(bind=engine)()


async def _register(client, teacher_token, tz="Asia/Kolkata", token=TOKEN):
    return await client.put("/teacher/device-token", headers={"Authorization": teacher_token}, json={
        "token": token, "platform": "android", "timezone": tz,
    })


async def test_brief_open_counted_once_and_in_summary(client, teacher_token, admin_token, period):
    auth = {"Authorization": teacher_token}
    url = f"/teacher/timetable/{period.id}/brief/opened"
    for source in ("push", "dashboard"):
        resp = await client.post(url, headers=auth, json={"on": date.today().isoformat(), "source": source})
        assert resp.status_code == 204

    resp = await client.get("/admin/system/report-feedback", headers={"Authorization": admin_token})
    adoption = resp.json()["adoption"]
    assert adoption["brief_opens"] == 1
    assert adoption["brief_classes"] >= 1


async def test_device_token_register_move_remove(client, teacher_token, period):
    assert (await _register(client, teacher_token, tz="Mars/Olympus")).status_code == 422
    assert (await _register(client, teacher_token)).status_code == 204
    assert (await _register(client, teacher_token, tz="Asia/Kolkata")).status_code == 204
    async with AsyncSessionLocal() as session:
        assert len((await session.execute(select(DeviceToken))).scalars().all()) == 1

    resp = await client.request("DELETE", "/teacher/device-token", headers={"Authorization": teacher_token},
                                json={"token": TOKEN})
    assert resp.status_code == 204
    async with AsyncSessionLocal() as session:
        assert (await session.execute(select(DeviceToken))).scalars().all() == []


async def test_reminder_sent_once_in_local_time(client, teacher_token, subject, teacher_user, period):
    await _register(client, teacher_token)
    rec = await _add(Recording(
        subject_id=subject.id, teacher_id=teacher_user.id, cloudinary_url="x", cloudinary_public_id="x",
        status=RecordingStatus.completed, uploaded_at=datetime(2026, 10, 28),
    ))
    await _add(LLMReport(recording_id=rec.id, raw_llm_response={
        "class_summary": {"covered": ["Fractions"], "unfinished": [], "recall_questions": ["What is 1/2 of 10?"]},
    }))

    sent: list[list[dict]] = []
    claimed: set[str] = set()

    def claim(key):
        if key in claimed:
            return False
        claimed.add(key)
        return True

    def send(messages):
        sent.append(messages)
        return [{"status": "ok"} for _ in messages]

    with _sync_session() as session:
        assert send_due_reminders(session, claim, send, now=NOW) == 1
        assert send_due_reminders(session, claim, send, now=NOW) == 0
        # At 08:20 local the class is 40 minutes off: too early.
        assert send_due_reminders(session, lambda k: True, send, now=NOW.replace(hour=2, minute=50)) == 0

    message = sent[0][0]
    assert message["to"] == TOKEN
    assert message["title"].endswith("in 10 min")
    assert message["body"] == "Open with: What is 1/2 of 10?"
    assert message["data"] == {"type": "class_brief", "timetable_entry_id": period.id, "on": "2026-10-29"}


async def test_muted_class_gets_no_reminder(client, teacher_token, subject, teacher_user, period):
    await _register(client, teacher_token)
    await _add(ClassBriefMute(teacher_id=teacher_user.id, subject_id=subject.id))
    with _sync_session() as session:
        assert send_due_reminders(session, lambda k: True, lambda m: [], now=NOW) == 0


async def test_unregistered_token_is_dropped(client, teacher_token, period):
    await _register(client, teacher_token)

    def send(messages):
        return [{"status": "error", "details": {"error": "DeviceNotRegistered"}} for _ in messages]

    with _sync_session() as session:
        assert send_due_reminders(session, lambda k: True, send, now=NOW) == 1
    async with AsyncSessionLocal() as session:
        assert (await session.execute(select(DeviceToken))).scalars().all() == []
