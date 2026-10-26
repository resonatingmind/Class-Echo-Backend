"""
Tests for teacher feedback on report findings (👍 / 👎 with reasons).

What these tests cover:
- A teacher can agree / disagree with a strength, dimension or improvement, and undo it
- Re-sending replaces the earlier verdict; reasons are dropped on an agree
- Unknown findings are rejected; another teacher's report is a 404
- Feedback is shown on the report to the school admin, and never changes the score
- The super admin summary counts disagreements per dimension and per reason
"""
import pytest_asyncio

from app.core.security import create_access_token, hash_password
from app.models.llm_report import LLMReport
from app.models.recording import Recording, RecordingStatus
from app.models.teacher import Teacher
from app.services.llm_pipeline.config import RUBRIC_VERSION, SCORE_WEIGHTS


@pytest_asyncio.fixture
async def report(db_session, subject, teacher_user):
    recording = Recording(
        subject_id=subject.id,
        teacher_id=teacher_user.id,
        cloudinary_url="https://fake.cloudinary.com/feedback.mp3",
        cloudinary_public_id="fake/feedback",
        status=RecordingStatus.completed,
    )
    db_session.add(recording)
    await db_session.flush()
    report = LLMReport(
        recording_id=recording.id,
        overall_score=62,
        teaching_quality_notes="ok",
        score_breakdown={k: {"score": 1, "finding": "f", "evidence": []} for k in SCORE_WEIGHTS},
        quantitative_metrics={"top_strengths": ["s1", "s2"], "priority_improvements": ["i1"]},
        raw_llm_response={
            "strengths": [
                {"area": "verbal_clarity", "text": "s1", "evidence": "q1"},
                {"area": "conceptual_depth", "text": "s2", "evidence": "q2"},
            ],
            "improvements": [
                {"area": "student_engagement", "observation": "i1", "try_this": "t", "why": "w", "is_focus": True},
            ],
        },
        rubric_version=RUBRIC_VERSION,
    )
    db_session.add(report)
    await db_session.commit()
    return report


def _url(report):
    return f"/teacher/recordings/{report.recording_id}/feedback"


async def test_disagree_then_change_mind_then_undo(client, teacher_token, report):
    auth = {"Authorization": teacher_token}
    resp = await client.put(_url(report), headers=auth, json={
        "target_type": "improvement", "target_key": "0", "verdict": "disagree",
        "reasons": ["class_type", "score_too_low", "class_type"], "comment": "  Revision class  ",
    })
    assert resp.status_code == 200
    [fb] = resp.json()
    assert fb["area"] == "student_engagement"
    assert fb["reasons"] == ["class_type", "score_too_low"]
    assert fb["comment"] == "Revision class"

    # Switching to agree replaces the row and drops the reasons.
    resp = await client.put(_url(report), headers=auth, json={
        "target_type": "improvement", "target_key": "0", "verdict": "agree", "reasons": ["other"],
    })
    [fb] = resp.json()
    assert (fb["verdict"], fb["reasons"], fb["comment"]) == ("agree", [], None)

    resp = await client.delete(f"{_url(report)}/improvement/0", headers=auth)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_dimension_feedback_uses_dimension_as_area(client, teacher_token, report):
    resp = await client.put(_url(report), headers={"Authorization": teacher_token}, json={
        "target_type": "dimension", "target_key": "closure_recap", "verdict": "disagree",
    })
    assert resp.json()[0]["area"] == "closure_recap"


async def test_unknown_findings_are_rejected(client, teacher_token, report):
    auth = {"Authorization": teacher_token}
    for target_type, key in [("strength", "2"), ("improvement", "x"), ("dimension", "charisma")]:
        resp = await client.put(_url(report), headers=auth, json={
            "target_type": target_type, "target_key": key, "verdict": "agree",
        })
        assert resp.status_code == 422, (target_type, key)
    resp = await client.put(_url(report), headers=auth, json={
        "target_type": "strength", "target_key": "0", "verdict": "disagree", "reasons": ["made_up"],
    })
    assert resp.status_code == 422


async def test_other_teacher_cannot_give_feedback(client, db_session, school, report):
    other = Teacher(
        name="Other", email="other-fb@test.com", hashed_password=hash_password("pw123456"),
        school_id=school.id, status="active",
    )
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)
    token = f"Bearer {create_access_token({'sub': str(other.id), 'role': 'teacher'})}"
    resp = await client.put(_url(report), headers={"Authorization": token}, json={
        "target_type": "strength", "target_key": "0", "verdict": "disagree",
    })
    assert resp.status_code == 404


async def test_school_admin_sees_disagreement_and_score_is_unchanged(
    client, teacher_token, school_admin_token, report,
):
    await client.put(_url(report), headers={"Authorization": teacher_token}, json={
        "target_type": "strength", "target_key": "1", "verdict": "disagree", "reasons": ["quote_out_of_context"],
    })
    resp = await client.get(
        f"/school/recordings/{report.recording_id}", headers={"Authorization": school_admin_token},
    )
    assert resp.status_code == 200
    data = resp.json()["report"]
    assert data["overall_score"] == 62
    assert data["feedback"][0]["target_key"] == "1"
    assert data["feedback"][0]["verdict"] == "disagree"
    assert data["strengths"][1]["evidence"] == "q2"


async def test_admin_summary_counts_by_area_and_reason(client, teacher_token, admin_token, report):
    auth = {"Authorization": teacher_token}
    await client.put(_url(report), headers=auth, json={
        "target_type": "improvement", "target_key": "0", "verdict": "disagree", "reasons": ["class_type"],
    })
    await client.put(_url(report), headers=auth, json={
        "target_type": "dimension", "target_key": "verbal_clarity", "verdict": "agree",
    })
    resp = await client.get("/admin/system/report-feedback", headers={"Authorization": admin_token})
    assert resp.status_code == 200
    data = resp.json()
    assert (data["teachers_total"], data["feedback_total"], data["disagree_total"]) == (1, 2, 1)
    areas = {a["area"]: a for a in data["by_area"]}
    assert areas["student_engagement"]["teacher_rate"] == 1.0
    assert areas["student_engagement"]["flagged"] is True
    assert areas["verbal_clarity"]["agree"] == 1 and not areas["verbal_clarity"]["flagged"]
    assert data["by_reason"] == [{"reason": "class_type", "count": 1}]
    assert data["recent"][0]["area"] == "student_engagement"
