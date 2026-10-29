"""
Tests for the self-rating step, report open tracking and clip-play counting.

What these tests cover:
- A teacher rates their lecture (or skips) once per report; a second try is a 409
- Invalid ratings are rejected
- The self-rating is shown to the teacher, never to the school admin
- The teacher opening a report records first_opened_at; a school admin opening it does not
- Clip plays are counted, and the super admin summary reports adoption numbers
"""
import pytest_asyncio
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.llm_report import LLMReport
from app.models.recording import Recording, RecordingStatus
from app.services.llm_pipeline.config import RUBRIC_VERSION, SCORE_WEIGHTS


@pytest_asyncio.fixture
async def report(db_session, subject, teacher_user):
    recording = Recording(
        subject_id=subject.id,
        teacher_id=teacher_user.id,
        cloudinary_url="https://fake.cloudinary.com/rating.mp3",
        cloudinary_public_id="fake/rating",
        status=RecordingStatus.completed,
        transcription="Hello Hi",
        transcript_segments=[
            {"id": 0, "start": 0.0, "end": 8.0, "speaker": "teacher", "text": "Hello"},
            {"id": 1, "start": 8.0, "end": 10.0, "speaker": "student", "text": "Hi"},
        ],
    )
    db_session.add(recording)
    await db_session.flush()
    report = LLMReport(
        recording_id=recording.id,
        overall_score=70,
        teaching_quality_notes="ok",
        score_breakdown={k: {"score": 1, "finding": "f", "evidence": []} for k in SCORE_WEIGHTS},
        quantitative_metrics={},
        raw_llm_response={
            "improvements": [
                {"area": "closure_recap", "observation": "o", "try_this": "t", "why": "w", "is_focus": True,
                 "segment_id": 1, "start": 8.0},
            ],
            "highlights": {"best_moment": {"segment_id": 0, "start": 0.0, "note": "n"}},
        },
        rubric_version=RUBRIC_VERSION,
    )
    db_session.add(report)
    await db_session.commit()
    return report


def _url(report, path=""):
    return f"/teacher/recordings/{report.recording_id}{path}"


async def _first_opened_at(_db_session, report):
    # A fresh session: reusing the fixture's session after API calls leaves a
    # connection that fails the fixture's teardown.
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(LLMReport.first_opened_at).where(LLMReport.id == report.id))
        return result.scalar_one()


async def test_rate_once_then_conflict(client, teacher_token, report):
    auth = {"Authorization": teacher_token}
    resp = await client.put(_url(report, "/self-rating"), headers=auth, json={
        "ratings": {"clarity": 4, "recap": 2}, "reflection": "  More recap  ",
    })
    assert resp.status_code == 201
    assert resp.json()["ratings"] == {"clarity": 4, "recap": 2}
    assert resp.json()["reflection"] == "More recap"

    resp = await client.put(_url(report, "/self-rating"), headers=auth, json={"skipped": True})
    assert resp.status_code == 409


async def test_skip_drops_ratings(client, teacher_token, report):
    resp = await client.put(_url(report, "/self-rating"), headers={"Authorization": teacher_token}, json={
        "skipped": True, "ratings": {"clarity": 5}, "reflection": "x",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert (data["ratings"], data["reflection"], data["skipped"]) == ({}, None, True)


async def test_invalid_ratings_rejected(client, teacher_token, report):
    auth = {"Authorization": teacher_token}
    for body in ({"ratings": {"charm": 3}}, {"ratings": {"clarity": 6}}, {"ratings": {}}):
        resp = await client.put(_url(report, "/self-rating"), headers=auth, json=body)
        assert resp.status_code == 422, body


async def test_teacher_open_is_recorded_and_rating_is_private(
    client, db_session, teacher_token, school_admin_token, report,
):
    await client.put(_url(report, "/self-rating"), headers={"Authorization": teacher_token}, json={
        "ratings": {"engagement": 3},
    })

    resp = await client.get(
        f"/school/recordings/{report.recording_id}", headers={"Authorization": school_admin_token},
    )
    assert resp.status_code == 200
    assert resp.json()["report"]["self_rating"] is None
    assert await _first_opened_at(db_session, report) is None

    resp = await client.get(_url(report), headers={"Authorization": teacher_token})
    data = resp.json()
    assert data["report"]["self_rating"]["ratings"] == {"engagement": 3}
    assert data["transcript_segments"][1]["speaker"] == "student"
    assert data["report"]["improvements"][0]["start"] == 8.0
    assert data["report"]["highlights"]["best_moment"]["start"] == 0.0
    assert await _first_opened_at(db_session, report) is not None


async def test_clip_plays_and_adoption_summary(client, teacher_token, admin_token, report):
    auth = {"Authorization": teacher_token}
    await client.get(_url(report), headers=auth)
    for _ in range(2):
        assert (await client.post(_url(report, "/clip-plays"), headers=auth)).status_code == 204
    await client.put(_url(report, "/feedback"), headers=auth, json={
        "target_type": "improvement", "target_key": "0", "verdict": "agree",
    })
    await client.put(_url(report, "/self-rating"), headers=auth, json={"skipped": True})

    resp = await client.get("/admin/system/report-feedback", headers={"Authorization": admin_token})
    adoption = resp.json()["adoption"]
    assert adoption["open_rate"] == 1.0
    assert adoption["clip_play_rate"] == 1.0
    assert (adoption["try_this_total"], adoption["try_this_useful"]) == (1, 1)
    assert (adoption["self_ratings_given"], adoption["self_ratings_skipped"]) == (0, 1)
    assert adoption["self_rating_completion"] == 0.0
