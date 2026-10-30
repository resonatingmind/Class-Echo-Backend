"""Push a reminder to the teacher's phone about 10 minutes before a timetabled class.

A Celery beat job calls send_due_reminders every 5 minutes. Timetable times
are the school's local clock, so each teacher's day and time come from the
timezone their phone registered with. Each class is reminded once per day:
claim() guards that across overlapping runs. Muted classes get nothing.

The notification carries one line from the brief (a recall question, else the
focus) so it is useful even unopened; tapping it opens the dashboard brief.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.models.class_brief_mute import ClassBriefMute
from app.models.device_token import DeviceToken
from app.models.recording import Recording
from app.models.timetable_entry import TimetableEntry
from app.services.class_brief_service import _completed_with_report, _summary_of

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
# A class is reminded when it starts between these two leads from now. The
# window is wider than the 5-minute beat, so every class falls in one run.
EARLIEST_LEAD = timedelta(minutes=5)
LATEST_LEAD = timedelta(minutes=12, seconds=30)
EXPO_BATCH = 100
# The app creates this Android channel (CLASS_REMINDER_CHANNEL in lib/classReminders.ts).
ANDROID_CHANNEL = "class-reminders"


@dataclass
class Reminder:
    entry_id: int
    teacher_id: int
    on: date
    title: str
    body: str
    tokens: list[str] = field(default_factory=list)


def _teacher_zone(devices: list[DeviceToken]) -> ZoneInfo | None:
    latest = max(devices, key=lambda d: d.updated_at or datetime.min)
    try:
        return ZoneInfo(latest.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _brief_line(session: Session, subject_id: int, teacher_id: int) -> str:
    """One useful line from the brief, without building all of it."""
    for rec in session.scalars(_completed_with_report().where(Recording.subject_id == subject_id).limit(5)):
        if rec.report:
            _, _, recall = _summary_of(rec.report.raw_llm_response)
            if recall:
                return f"Open with: {recall[0]}"
    for rec in session.scalars(_completed_with_report().where(Recording.teacher_id == teacher_id).limit(5)):
        improvements = ((rec.report.raw_llm_response or {}).get("improvements") if rec.report else None) or []
        focus = next((i for i in improvements if isinstance(i, dict) and i.get("is_focus") and i.get("try_this")), None)
        if focus:
            return f"Your focus today: {focus['try_this']}"
    return "Tap for your recap before class."


def due_reminders(session: Session, now: datetime | None = None) -> list[Reminder]:
    """Classes starting in about 10 minutes, per teacher with a registered phone."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    devices_by_teacher: dict[int, list[DeviceToken]] = defaultdict(list)
    for device in session.scalars(select(DeviceToken)):
        devices_by_teacher[device.teacher_id].append(device)

    reminders: list[Reminder] = []
    for teacher_id, devices in devices_by_teacher.items():
        zone = _teacher_zone(devices)
        if zone is None:
            continue
        local = now.astimezone(zone)
        muted = set(session.scalars(
            select(ClassBriefMute.subject_id).where(ClassBriefMute.teacher_id == teacher_id)
        ))
        entries = session.scalars(
            select(TimetableEntry)
            .options(selectinload(TimetableEntry.class_), selectinload(TimetableEntry.subject))
            .where(TimetableEntry.teacher_id == teacher_id, TimetableEntry.day_of_week == local.strftime("%A"))
        )
        for entry in entries:
            if entry.subject_id in muted:
                continue
            lead = datetime.combine(local.date(), entry.start_time, tzinfo=zone) - local
            if not EARLIEST_LEAD <= lead <= LATEST_LEAD:
                continue
            class_name = " ".join(p for p in (entry.class_.name, entry.class_.section) if p) if entry.class_ else ""
            subject = entry.subject.name if entry.subject else "Class"
            minutes = round(lead.total_seconds() / 60)
            reminders.append(Reminder(
                entry_id=entry.id,
                teacher_id=teacher_id,
                on=local.date(),
                title=f"{class_name} {subject} in {minutes} min".strip(),
                body=_brief_line(session, entry.subject_id, teacher_id),
                tokens=[d.token for d in devices],
            ))
    return reminders


def _expo_send(messages: list[dict]) -> list[dict]:
    """Send to Expo's push service; returns one ticket per message."""
    tickets: list[dict] = []
    with httpx.Client(timeout=20) as client:
        for i in range(0, len(messages), EXPO_BATCH):
            resp = client.post(EXPO_PUSH_URL, json=messages[i:i + EXPO_BATCH])
            resp.raise_for_status()
            tickets.extend(resp.json().get("data") or [])
    return tickets


def send_due_reminders(
    session: Session,
    claim: Callable[[str], bool],
    send: Callable[[list[dict]], list[dict]] = _expo_send,
    now: datetime | None = None,
) -> int:
    """Send every due reminder not already sent today. Returns how many classes were reminded.

    claim(key) must return True only the first time a key is seen that day.
    """
    messages: list[dict] = []
    reminded = 0
    for r in due_reminders(session, now):
        if not claim(f"class-reminder:{r.entry_id}:{r.on.isoformat()}"):
            continue
        reminded += 1
        data = {"type": "class_brief", "timetable_entry_id": r.entry_id, "on": r.on.isoformat()}
        messages.extend(
            {
                "to": token, "title": r.title, "body": r.body, "data": data,
                "sound": "default", "priority": "high", "channelId": ANDROID_CHANNEL,
            }
            for token in r.tokens
        )
    if not messages:
        return reminded

    tickets = send(messages)
    # Phones that uninstalled the app or turned notifications off stop getting pushes.
    dead = [
        m["to"] for m, t in zip(messages, tickets)
        if t.get("status") == "error" and (t.get("details") or {}).get("error") == "DeviceNotRegistered"
    ]
    if dead:
        session.execute(delete(DeviceToken).where(DeviceToken.token.in_(dead)))
        session.commit()
    logger.info("Class reminders: %d classes, %d pushes, %d dead tokens", reminded, len(messages), len(dead))
    return reminded
