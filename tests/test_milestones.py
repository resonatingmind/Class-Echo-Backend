"""
Tests for week streaks, milestones, the school admin growth view and the report lock.

What these tests cover:
- Streaks count weeks with enough recordings; weeks nobody at the school recorded are skipped;
  the week under way never breaks a streak
- Milestones appear from the teacher's data, are private until shared, and the admin sees only shared ones
- The school's streak threshold can be changed by its admin
- The school admin sees growth and status, never per-lecture reports, scores, transcripts or the ranking
"""
from datetime import date, datetime, timedelta

import pytest_asyncio

from app.database import AsyncSessionLocal
from app.models.llm_report import LLMReport
from app.models.recording import Recording, RecordingStatus
from app.services.llm_pipeline.config import RUBRIC_VERSION, SCORE_WEIGHTS
from app.services.milestone_service import streak_history, week_of

MONDAY = date(2026, 10, 5)


def _at(week: int, day: int = 0) -> datetime:
    return datetime.combine(MONDAY + timedelta(weeks=week, days=day), datetime.min.time()).replace(hour=10)


def test_streak_counts_weeks_and_skips_holidays():
    school = {week_of(_at(w)) for w in (0, 1, 3, 4)}  # week 2 nobody recorded: a holiday
    teacher = [_at(0), _at(0, 2), _at(1), _at(1, 1), _at(3), _at(3, 3), _at(4)]
    # Week 4 is the current week with only one recording so far: still under way.
    history = streak_history(teacher, school, threshold=2, today=MONDAY + timedelta(weeks=4, days=1))
    assert history.current == 3
    assert history.this_week == 1
    assert history.reached_milestone_at is None


def test_streak_breaks_on_a_short_school_week():
    school = {week_of(_at(w)) for w in range(4)}
    teacher = [_at(0), _at(0, 1), _at(1), _at(2), _at(2, 1), _at(3), _at(3, 1)]
    history = streak_history(teacher, school, threshold=2, today=MONDAY + timedelta(weeks=3, days=4))
    assert (history.current, history.best) == (2, 2)


def test_four_week_streak_reached():
    school = {week_of(_at(w)) for w in range(5)}
    teacher = [t for w in range(4) for t in (_at(w), _at(w, 2))]
    history = streak_history(teacher, school, threshold=2, today=MONDAY + timedelta(weeks=4))
    assert history.current == 4
    assert history.reached_milestone_at == _at(3, 2)


async def _add(*rows):
    async with AsyncSessionLocal() as session:
        session.add_all(rows)
        await session.commit()
        for row in rows:
            await session.refresh(row)
    return rows[0]


@pytest_asyncio.fixture
async def lectures(subject, teacher_user):
    """Ten scored lectures, the last with its best engagement yet."""
    recordings = []
    for i in range(10):
        rec = await _add(Recording(
            subject_id=subject.id, teacher_id=teacher_user.id, cloudinary_url=f"x{i}", cloudinary_public_id="x",
            status=RecordingStatus.completed, uploaded_at=datetime(2026, 9, 1) + timedelta(days=3 * i),
            transcription="t",
            transcript_segments=[{"id": 0, "start": 0, "end": 30, "speaker": "teacher", "text": "Hello"}],
        ))
        engagement = 15 if i == 9 else 8
        await _add(LLMReport(
            recording_id=rec.id, overall_score=60 + i, rubric_version=RUBRIC_VERSION,
            score_breakdown={k: {"score": engagement if k == "student_engagement" else 3, "finding": "f", "evidence": []}
                             for k in SCORE_WEIGHTS},
            raw_llm_response={}, teaching_quality_notes="secret notes",
        ))
        recordings.append(rec)
    return recordings


async def test_milestones_private_until_shared(client, teacher_token, school_admin_token, teacher_user, lectures):
    teacher = {"Authorization": teacher_token}
    admin = {"Authorization": school_admin_token}

    growth = (await client.get("/teacher/growth", headers=teacher)).json()
    keys = {m["key"] for m in growth["milestones"]}
    assert {"first_recording", "ten_lectures", "best_engagement"} <= keys
    assert growth["streak"]["threshold"] == 2

    resp = await client.get(f"/school/teachers/{teacher_user.id}/growth", headers=admin)
    assert resp.status_code == 200
    assert resp.json()["growth"]["milestones"] == []
    assert resp.json()["status"] == "improving"

    ten = next(m for m in growth["milestones"] if m["key"] == "ten_lectures")
    resp = await client.put(f"/teacher/milestones/{ten['id']}", headers=teacher, json={"shared": True})
    assert resp.json()["shared"] is True
    resp = await client.get(f"/school/teachers/{teacher_user.id}/growth", headers=admin)
    assert [m["key"] for m in resp.json()["growth"]["milestones"]] == ["ten_lectures"]

    # Loading again does not duplicate anything.
    again = (await client.get("/teacher/growth", headers=teacher)).json()
    assert len(again["milestones"]) == len(growth["milestones"])


async def test_school_growth_has_no_ranking(client, school_admin_token, teacher_user, lectures):
    admin = {"Authorization": school_admin_token}
    data = (await client.get("/school/growth", headers=admin)).json()
    assert [t["teacher_id"] for t in data["teachers"]] == [teacher_user.id]
    assert data["teachers"][0]["status"] == "improving"
    assert data["status_counts"] == {"improving": 1}
    assert data["teachers_counted"] == 1
    assert (await client.get("/school/dashboard/teacher-ranking", headers=admin)).status_code == 410


async def test_admin_cannot_read_reports(client, school_admin_token, teacher_token, subject, teacher_user, lectures):
    admin = {"Authorization": school_admin_token}
    rec = lectures[-1]

    detail = (await client.get(f"/school/recordings/{rec.id}", headers=admin)).json()
    assert detail["report"] is None and detail["report_private"] is True
    assert detail["transcription"] is None and detail["transcript_segments"] == []
    assert detail["cloudinary_url"] == ""

    rows = (await client.get(f"/school/teachers/{teacher_user.id}/recordings", headers=admin)).json()
    assert rows and all(r["overall_score"] is None and r["cloudinary_url"] == "" for r in rows)
    rows = (await client.get(f"/school/subjects/{subject.id}/recordings", headers=admin)).json()
    assert rows and all(r["report"] is None for r in rows)
    assert (await client.get(f"/school/subjects/{subject.id}/reports", headers=admin)).status_code == 403
    assert (await client.get(f"/school/teachers/{teacher_user.id}/analytics", headers=admin)).status_code == 403

    # A shared moment brings back the audio and only the lines around it.
    await client.put(f"/teacher/recordings/{rec.id}/shared-clips", headers={"Authorization": teacher_token},
                     json={"start": 5, "title": "Best moment"})
    detail = (await client.get(f"/school/recordings/{rec.id}", headers=admin)).json()
    assert detail["cloudinary_url"] and len(detail["transcript_segments"]) == 1
    assert [c["start"] for c in detail["shared_clips"]] == [5.0]
    growth = (await client.get(f"/school/teachers/{teacher_user.id}/growth", headers=admin)).json()
    assert [c["recording_id"] for c in growth["shared_clips"]] == [rec.id]


async def test_admin_sets_streak_threshold(client, school_admin_token):
    admin = {"Authorization": school_admin_token}
    assert (await client.put("/school/me", headers=admin, json={"streak_min_recordings": 0})).status_code == 422
    resp = await client.put("/school/me", headers=admin, json={"streak_min_recordings": 3})
    assert resp.json()["streak_min_recordings"] == 3
