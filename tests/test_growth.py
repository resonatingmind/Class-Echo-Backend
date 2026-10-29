"""
Tests for My Growth trends.

What these tests cover:
- Each area is compared as last 5 lectures vs the 5 before, on a 0-100 scale
- Too few lectures: an average but no comparison
- Only reports under the current rubric count; older ones are counted separately
- Focus progress: recent lectures above the earlier average in the focus area
- A lecture can be left out only after a doesn't-fit-this-class dispute, and goes
  back in when that dispute is taken back
- Another teacher's lectures never appear
"""
from datetime import datetime, timedelta

from app.core.security import create_access_token, hash_password
from app.database import AsyncSessionLocal
from app.models.llm_report import LLMReport
from app.models.recording import Recording, RecordingStatus
from app.models.teacher import Teacher
from app.services.llm_pipeline.config import RUBRIC_VERSION, SCORE_WEIGHTS


async def _add(*rows):
    # Own session: the db_session fixture fails when used from a test body.
    async with AsyncSessionLocal() as session:
        session.add_all(rows)
        await session.commit()
        for row in rows:
            await session.refresh(row)
    return rows[0]


async def _lecture(subject, teacher_id, day: int, engagement: int, *, rubric=RUBRIC_VERSION, focus_area=None):
    """One completed lecture; every area scores half marks except student_engagement."""
    rec = await _add(Recording(
        subject_id=subject.id,
        teacher_id=teacher_id,
        cloudinary_url="https://fake.cloudinary.com/growth.mp3",
        cloudinary_public_id="fake/growth",
        status=RecordingStatus.completed,
        uploaded_at=datetime(2026, 9, 1) + timedelta(days=day),
    ))
    breakdown = {k: {"score": mx // 2, "finding": "f", "evidence": []} for k, mx in SCORE_WEIGHTS.items()}
    breakdown["student_engagement"]["score"] = engagement
    improvements = [{
        "area": focus_area or "student_engagement", "observation": "o", "try_this": "Wait five seconds",
        "why": "w", "is_focus": True,
    }]
    await _add(LLMReport(
        recording_id=rec.id,
        overall_score=sum(v["score"] for v in breakdown.values()),
        teaching_quality_notes="ok",
        score_breakdown=breakdown,
        quantitative_metrics={},
        raw_llm_response={"improvements": improvements},
        rubric_version=rubric,
    ))
    return rec


def _area(body, area):
    return next(a for a in body["areas"] if a["area"] == area)


async def test_area_trend_compares_two_windows(client, subject, teacher_user, teacher_token):
    # Engagement (max 15): 6 for the first five lectures, then 12.
    for day in range(10):
        await _lecture(subject, teacher_user.id, day, 6 if day < 5 else 12)

    resp = await client.get("/teacher/growth", headers={"Authorization": teacher_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["lecture_count"] == 10
    engagement = _area(body, "student_engagement")
    assert engagement["previous_avg"] == 40.0
    assert engagement["recent_avg"] == 80.0
    assert engagement["change"] == 40.0
    assert engagement["direction"] == "improving"
    assert len(engagement["series"]) == 10
    assert _area(body, "verbal_clarity")["direction"] == "steady"
    # Focus area engagement: the last 4 all beat the earlier average.
    assert body["focus"]["area"] == "student_engagement"
    assert (body["focus"]["recent"], body["focus"]["improved"]) == (4, 4)


async def test_too_few_lectures_gives_no_comparison(client, subject, teacher_user, teacher_token):
    for day in range(4):
        await _lecture(subject, teacher_user.id, day, 9)

    body = (await client.get("/teacher/growth", headers={"Authorization": teacher_token})).json()
    engagement = _area(body, "student_engagement")
    assert engagement["recent_avg"] == 60.0
    assert engagement["change"] is None
    assert engagement["direction"] is None


async def test_only_the_current_rubric_counts(client, subject, teacher_user, teacher_token):
    for day in range(3):
        await _lecture(subject, teacher_user.id, day, 3, rubric="1")
    await _lecture(subject, teacher_user.id, 5, 9)

    body = (await client.get("/teacher/growth", headers={"Authorization": teacher_token})).json()
    assert body["lecture_count"] == 1
    assert body["older_rubric_count"] == 3
    assert _area(body, "student_engagement")["recent_avg"] == 60.0


async def test_exclusion_needs_a_class_type_dispute(client, subject, teacher_user, teacher_token):
    auth = {"Authorization": teacher_token}
    rec = await _lecture(subject, teacher_user.id, 0, 9)
    await _lecture(subject, teacher_user.id, 1, 9)

    resp = await client.put(f"/teacher/recordings/{rec.id}/trends", headers=auth, json={"excluded": True})
    assert resp.status_code == 409

    dispute = {"target_type": "dimension", "target_key": "student_engagement", "verdict": "disagree",
               "reasons": ["class_type"]}
    assert (await client.put(f"/teacher/recordings/{rec.id}/feedback", headers=auth, json=dispute)).status_code == 200
    resp = await client.put(f"/teacher/recordings/{rec.id}/trends", headers=auth, json={"excluded": True})
    assert resp.status_code == 200
    assert resp.json() == {"excluded_from_trends": True}

    body = (await client.get("/teacher/growth", headers=auth)).json()
    assert (body["lecture_count"], body["excluded_count"]) == (1, 1)
    detail = (await client.get(f"/teacher/recordings/{rec.id}", headers=auth)).json()
    assert detail["report"]["excluded_from_trends"] is True

    # Taking the dispute back puts the lecture back in.
    resp = await client.delete(f"/teacher/recordings/{rec.id}/feedback/dimension/student_engagement", headers=auth)
    assert resp.status_code == 200
    body = (await client.get("/teacher/growth", headers=auth)).json()
    assert (body["lecture_count"], body["excluded_count"]) == (2, 0)


async def test_other_teachers_lectures_are_not_included(client, school, subject, teacher_user):
    other = await _add(Teacher(name="Other", email="other@test.com", hashed_password=hash_password("x"),
                               school_id=school.id, status="active"))
    rec = await _lecture(subject, teacher_user.id, 0, 9)
    token = {"Authorization": "Bearer " + create_access_token({"sub": str(other.id), "role": "teacher"})}

    body = (await client.get("/teacher/growth", headers=token)).json()
    assert body["lecture_count"] == 0
    assert body["focus"] is None
    assert (await client.put(f"/teacher/recordings/{rec.id}/trends", headers=token,
                             json={"excluded": False})).status_code == 404
