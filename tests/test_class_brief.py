"""
Tests for the next-class brief.

What these tests cover:
- The scorer's class_summary is cleaned up and stored with the report
- The brief shows the last lesson's summary, today's plan and the teacher's focus
- Reports from before the class summary fall back to curriculum coverage
- A substitute teacher gets the class's last summary, marked as another teacher's
- No recording yet: plan only
- Muting hides the brief for that class; another teacher's period is a 404
"""
from datetime import datetime, time

import pytest_asyncio

from app.core.security import create_access_token, hash_password
from app.database import AsyncSessionLocal
from app.models.chapter import Chapter
from app.models.learning_plan import LearningPlan
from app.models.llm_report import LLMReport
from app.models.recording import Recording, RecordingStatus
from app.models.teacher import Teacher
from app.models.timetable_entry import TimetableEntry
from app.services.llm_pipeline.config import RUBRIC_VERSION, SCORE_WEIGHTS
from app.services.llm_pipeline.evaluator import _validate_and_fix


def test_class_summary_is_cleaned_up():
    result = _validate_and_fix({
        "scores": {},
        "class_summary": {
            "covered": [" Fractions ", "", "Equivalent fractions", "a", "b", "c"],
            "unfinished": "not a list",
            "recall_questions": ["What is 1/2 of 10?"],
        },
    })
    assert result["class_summary"] == {
        "covered": ["Fractions", "Equivalent fractions", "a", "b"],
        "unfinished": [],
        "recall_questions": ["What is 1/2 of 10?"],
    }
    assert _validate_and_fix({"scores": {}})["class_summary"] == {
        "covered": [], "unfinished": [], "recall_questions": [],
    }


# Helpers open their own session: the db_session fixture belongs to the fixture
# event loop and fails when used from a test body.
async def _add(*rows):
    async with AsyncSessionLocal() as session:
        session.add_all(rows)
        await session.commit()
        for row in rows:
            await session.refresh(row)
    return rows[0]


async def _teacher(school, email) -> Teacher:
    return await _add(Teacher(name=email, email=email, hashed_password=hash_password("x"),
                              school_id=school.id, status="active"))


def _token(teacher) -> dict:
    return {"Authorization": "Bearer " + create_access_token({"sub": str(teacher.id), "role": "teacher"})}


async def _period(school, class_, subject, teacher_id) -> TimetableEntry:
    return await _add(TimetableEntry(
        school_id=school.id, class_id=class_.id, subject_id=subject.id, teacher_id=teacher_id,
        day_of_week="Monday", period_number=1, start_time=time(9, 0), end_time=time(9, 40),
    ))


async def _recording(subject, teacher_id, raw, *, plan=None, chapter=None, at=None):
    rec = await _add(Recording(
        subject_id=subject.id,
        teacher_id=teacher_id,
        chapter_id=chapter.id if chapter else None,
        learning_plan_id=plan.id if plan else None,
        lecture_title="Fractions 1",
        cloudinary_url="https://fake.cloudinary.com/brief.mp3",
        cloudinary_public_id="fake/brief",
        status=RecordingStatus.completed,
        uploaded_at=at or datetime(2026, 10, 27, 9, 0),
    ))
    await _add(LLMReport(
        recording_id=rec.id,
        overall_score=70,
        teaching_quality_notes="ok",
        score_breakdown={k: {"score": 1, "finding": "f", "evidence": []} for k in SCORE_WEIGHTS},
        quantitative_metrics={},
        raw_llm_response=raw,
        rubric_version=RUBRIC_VERSION,
    ))
    return rec


SUMMARY_RAW = {
    "class_summary": {
        "covered": ["What a fraction is", "Halves and quarters"],
        "unfinished": ["Equivalent fractions"],
        "recall_questions": ["What is half of 8?"],
    },
    "improvements": [
        {"area": "student_engagement", "observation": "No wait time", "try_this": "Count to five", "why": "w",
         "is_focus": True},
    ],
}


@pytest_asyncio.fixture
async def plans(db_session, subject):
    chapter = Chapter(subject_id=subject.id, name="Fractions")
    db_session.add(chapter)
    await db_session.flush()
    first = LearningPlan(chapter_id=chapter.id, date="2026-10-27", topic_name="Fractions", sub_topic="Fractions 1")
    second = LearningPlan(chapter_id=chapter.id, date="2026-10-29", topic_name="Fractions", sub_topic="Fractions 2")
    third = LearningPlan(chapter_id=chapter.id, date=None, topic_name="Fractions", sub_topic="Fractions 3")
    db_session.add_all([first, second, third])
    await db_session.commit()
    return chapter, first, second, third


async def test_brief_has_summary_plan_and_focus(client, school, class_, subject, teacher_user,
                                                teacher_token, plans):
    chapter, first, second, _ = plans
    entry = await _period(school, class_, subject, teacher_user.id)
    await _recording(subject, teacher_user.id, SUMMARY_RAW, plan=first, chapter=chapter)

    resp = await client.get(f"/teacher/timetable/{entry.id}/brief?on=2026-10-29",
                            headers={"Authorization": teacher_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["muted"] is False
    assert body["last_lesson"]["covered"] == ["What a fraction is", "Halves and quarters"]
    assert body["last_lesson"]["unfinished"] == ["Equivalent fractions"]
    assert body["last_lesson"]["recall_questions"] == ["What is half of 8?"]
    assert body["last_lesson"]["by_other_teacher"] is False
    assert body["todays_plan"]["id"] == second.id
    assert body["focus"]["try_this"] == "Count to five"


async def test_old_report_falls_back_to_coverage(client, school, class_, subject, teacher_user,
                                                 teacher_token):
    entry = await _period(school, class_, subject, teacher_user.id)
    await _recording(subject, teacher_user.id, {
        "curriculum_coverage": {"covered_concepts": ["Halves"], "missing_concepts": ["Thirds"]},
    })

    body = (await client.get(f"/teacher/timetable/{entry.id}/brief",
                             headers={"Authorization": teacher_token})).json()
    assert body["last_lesson"]["covered"] == ["Halves"]
    assert body["last_lesson"]["unfinished"] == ["Thirds"]
    assert body["last_lesson"]["recall_questions"] == []
    assert body["focus"] is None


async def test_substitute_gets_the_class_summary(client, school, class_, subject, teacher_user,
                                                 plans):
    substitute = await _teacher(school, "sub@test.com")
    entry = await _period(school, class_, subject, substitute.id)
    await _recording(subject, teacher_user.id, SUMMARY_RAW, plan=plans[1], chapter=plans[0])

    body = (await client.get(f"/teacher/timetable/{entry.id}/brief", headers=_token(substitute))).json()
    assert body["last_lesson"]["by_other_teacher"] is True
    assert body["last_lesson"]["covered"][0] == "What a fraction is"
    # The focus is the substitute's own; they have no reports.
    assert body["focus"] is None


async def test_no_recording_gives_plan_only(client, school, class_, subject, teacher_user,
                                            teacher_token, plans):
    entry = await _period(school, class_, subject, teacher_user.id)
    body = (await client.get(f"/teacher/timetable/{entry.id}/brief?on=2026-11-02",
                             headers={"Authorization": teacher_token})).json()
    assert body["last_lesson"] is None
    # Nothing dated that day: the first open lecture in planned order.
    assert body["todays_plan"]["id"] == plans[1].id


async def test_mute_and_unmute(client, school, class_, subject, teacher_user, teacher_token):
    entry = await _period(school, class_, subject, teacher_user.id)
    await _recording(subject, teacher_user.id, SUMMARY_RAW)
    auth = {"Authorization": teacher_token}

    resp = await client.put(f"/teacher/timetable/{entry.id}/brief/mute", headers=auth, json={"muted": True})
    assert resp.status_code == 200
    assert resp.json()["muted"] is True
    assert resp.json()["last_lesson"] is None
    # Muting twice is fine.
    assert (await client.put(f"/teacher/timetable/{entry.id}/brief/mute", headers=auth,
                             json={"muted": True})).status_code == 200

    body = (await client.put(f"/teacher/timetable/{entry.id}/brief/mute", headers=auth,
                             json={"muted": False})).json()
    assert body["muted"] is False
    assert body["last_lesson"] is not None


async def test_other_teachers_period_is_not_found(client, school, class_, subject, teacher_user):
    entry = await _period(school, class_, subject, teacher_user.id)
    other = await _teacher(school, "other@test.com")

    assert (await client.get(f"/teacher/timetable/{entry.id}/brief", headers=_token(other))).status_code == 404
    assert (await client.put(f"/teacher/timetable/{entry.id}/brief/mute", headers=_token(other),
                             json={"muted": True})).status_code == 404
