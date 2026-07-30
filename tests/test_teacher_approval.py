import pytest
from httpx import AsyncClient
from sqlalchemy import select
from app.models.teacher import Teacher
from app.models.school import School
from app.core.security import create_access_token, hash_password


@pytest.mark.asyncio
async def test_teacher_registration_starts_pending(client: AsyncClient, db_session):
    # Setup: Create a school first
    school = School(name="Test Approval School", address="123 Test St")
    db_session.add(school)
    await db_session.commit()
    await db_session.refresh(school)

    # Register teacher
    response = await client.post(
        "/auth/teacher/register",
        json={
            "name": "Pending Teacher",
            "email": "pending_teacher@test.com",
            "password": "password123",
            "school_id": school.id,
        },
    )
    assert response.status_code == 201

    # Verify status in database
    result = await db_session.execute(
        select(Teacher).where(Teacher.email == "pending_teacher@test.com")
    )
    teacher = result.scalar_one()
    assert teacher.status == "pending_approval"


@pytest.mark.asyncio
async def test_pending_teacher_restricted_access(client: AsyncClient, db_session):
    # Setup: Create a pending teacher
    school = School(name="Test Approval School", address="123 Test St")
    db_session.add(school)
    await db_session.commit()
    await db_session.refresh(school)

    teacher = Teacher(
        name="Pending Teacher",
        email="pending_teacher@test.com",
        hashed_password=hash_password("password123"),
        school_id=school.id,
        status="pending_approval",
    )
    db_session.add(teacher)
    await db_session.commit()
    await db_session.refresh(teacher)

    token = create_access_token({"sub": str(teacher.id), "role": "teacher"})
    headers = {"Authorization": f"Bearer {token}"}

    # Can access /teacher/me (to see status)
    me_resp = await client.get("/teacher/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["status"] == "pending_approval"

    # Cannot access other endpoints (e.g. subjects, timetable)
    sub_resp = await client.get("/teacher/subjects", headers=headers)
    assert sub_resp.status_code == 403
    assert "not active" in sub_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_school_admin_teacher_management(client: AsyncClient, db_session, school_admin_token, school_id):
    headers = {"Authorization": f"Bearer {school_admin_token}"}

    # 1. Invite teacher
    invite_resp = await client.post(
        "/school/teachers/invite",
        headers=headers,
        json={"name": "Invited Teacher", "email": "invited_teacher@test.com"},
    )
    assert invite_resp.status_code == 200
    assert invite_resp.json()["status"] == "invited"

    teacher_id = invite_resp.json()["id"]

    # 2. Get list of invited teachers
    list_resp = await client.get("/school/teachers?status=invited", headers=headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["email"] == "invited_teacher@test.com"

    # 3. Accept invite / set password
    invite_token = create_access_token({"sub": str(teacher_id), "role": "teacher"})
    accept_resp = await client.post(
        "/auth/accept-invite",
        json={"token": invite_token, "password": "newpassword123"},
    )
    assert accept_resp.status_code == 200
    assert accept_resp.json()["role"] == "teacher"

    # Verify status in database is now active
    result = await db_session.execute(
        select(Teacher).where(Teacher.id == teacher_id)
    )
    teacher = result.scalar_one()
    assert teacher.status == "active"

    # 4. Suspend teacher
    suspend_resp = await client.post(
        f"/school/teachers/{teacher_id}/suspend", headers=headers
    )
    assert suspend_resp.status_code == 200
    assert suspend_resp.json()["status"] == "suspended"

    # 5. Reactivate teacher
    reactivate_resp = await client.post(
        f"/school/teachers/{teacher_id}/reactivate", headers=headers
    )
    assert reactivate_resp.status_code == 200
    assert reactivate_resp.json()["status"] == "active"

    # 6. Reject a pending teacher
    pending_teacher = Teacher(
        name="Pending Teacher",
        email="pending@test.com",
        hashed_password="",
        school_id=school_id,
        status="pending_approval",
    )
    db_session.add(pending_teacher)
    await db_session.commit()

    reject_resp = await client.post(
        f"/school/teachers/{pending_teacher.id}/reject",
        headers=headers,
        json={"reason": "Not recognized"},
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"
    assert reject_resp.json()["rejected_reason"] == "Not recognized"
