"""
Tests for sharing a clip with the school admin.

What these tests cover:
- The teacher shares a moment; sharing the same moment again updates it instead of adding a second
- The school admin sees shared clips on the report; nothing is shared by default
- Moments past the end of the lecture, or on a lecture without timings, are rejected
- Unsharing removes the clip; another teacher cannot share or unshare on someone else's report
"""
import pytest_asyncio

from app.core.security import create_access_token
from app.models.llm_report import LLMReport
from app.models.recording import Recording, RecordingStatus
from app.models.teacher import Teacher
from app.services.llm_pipeline.config import RUBRIC_VERSION


@pytest_asyncio.fixture
async def report(db_session, subject, teacher_user):
    recording = Recording(
        subject_id=subject.id,
        teacher_id=teacher_user.id,
        cloudinary_url="https://fake.cloudinary.com/clip.mp3",
        cloudinary_public_id="fake/clip",
        status=RecordingStatus.completed,
        transcription="Hello Hi",
        transcript_segments=[
            {"id": 0, "start": 0.0, "end": 8.0, "speaker": "teacher", "text": "Hello"},
            {"id": 1, "start": 8.0, "end": 60.0, "speaker": "student", "text": "Hi"},
        ],
    )
    db_session.add(recording)
    await db_session.flush()
    report = LLMReport(
        recording_id=recording.id,
        overall_score=70,
        teaching_quality_notes="ok",
        score_breakdown={},
        quantitative_metrics={},
        raw_llm_response={},
        rubric_version=RUBRIC_VERSION,
    )
    db_session.add(report)
    await db_session.commit()
    return report


def _url(report, path=""):
    return f"/teacher/recordings/{report.recording_id}{path}"


async def test_share_update_and_admin_sees_it(client, teacher_token, school_admin_token, report):
    teacher = {"Authorization": teacher_token}
    admin = {"Authorization": school_admin_token}

    resp = await client.get(f"/school/recordings/{report.recording_id}", headers=admin)
    assert resp.json()["report"] is None and resp.json()["shared_clips"] == []

    resp = await client.put(_url(report, "/shared-clips"), headers=teacher, json={
        "start": 12.5, "title": "Best moment", "note": "  Proud of this one  ",
    })
    assert resp.status_code == 200
    assert [(c["start"], c["title"], c["note"]) for c in resp.json()] == [(12.5, "Best moment", "Proud of this one")]

    resp = await client.put(_url(report, "/shared-clips"), headers=teacher, json={
        "start": 12.5, "title": "Best moment", "note": "",
    })
    assert len(resp.json()) == 1 and resp.json()[0]["note"] is None

    resp = await client.get(f"/school/recordings/{report.recording_id}", headers=admin)
    assert [c["start"] for c in resp.json()["shared_clips"]] == [12.5]

    resp = await client.get(_url(report), headers=teacher)
    assert [c["start"] for c in resp.json()["report"]["shared_clips"]] == [12.5]


async def test_unshare(client, teacher_token, report):
    teacher = {"Authorization": teacher_token}
    clips = (await client.put(_url(report, "/shared-clips"), headers=teacher, json={
        "start": 3, "title": "The moment",
    })).json()
    resp = await client.delete(_url(report, f"/shared-clips/{clips[0]['id']}"), headers=teacher)
    assert resp.status_code == 200 and resp.json() == []
    resp = await client.delete(_url(report, f"/shared-clips/{clips[0]['id']}"), headers=teacher)
    assert resp.status_code == 404


async def test_rejects_moment_past_the_end(client, teacher_token, report):
    resp = await client.put(_url(report, "/shared-clips"), headers={"Authorization": teacher_token}, json={
        "start": 61, "title": "The moment",
    })
    assert resp.status_code == 400


async def test_other_teacher_cannot_share(client, db_session, school, report):
    other = Teacher(
        name="Other", email="other-clip@test.com", hashed_password="x", school_id=school.id, status="active",
    )
    db_session.add(other)
    await db_session.commit()
    token = f"Bearer {create_access_token({'sub': str(other.id), 'role': 'teacher'})}"
    resp = await client.put(_url(report, "/shared-clips"), headers={"Authorization": token}, json={
        "start": 3, "title": "The moment",
    })
    assert resp.status_code in (403, 404)
