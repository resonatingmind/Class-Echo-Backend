import io
import pytest
from httpx import AsyncClient
from openpyxl import Workbook

from app.database import AsyncSessionLocal
from app.models.chapter import Chapter
from app.models.learning_plan import LearningPlan


def generate_test_excel(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


@pytest.mark.asyncio
async def test_download_learning_plan_template(client: AsyncClient):
    resp = await client.get("/public/learning-plans/subject-template")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "attachment" in resp.headers["content-disposition"]
    assert "subject_planner_template.xlsx" in resp.headers["content-disposition"]


@pytest.mark.asyncio
async def test_import_learning_plans_school_admin(client: AsyncClient, school_admin_token, subject):
    # Prepare valid excel sheet where 'Topic Name' acts directly as the 'Chapter Name'
    # Note: the chapter "Mendelian Genetics" does not exist yet. It will be auto-created by the service.
    excel_bytes = generate_test_excel([
        ["Topic Name", "Sub Topic", "Date", "Classwork Activities", "Assessment Task", "Teaching Aids", "Learning Outcomes"],
        ["Mendelian Genetics", "Punnett Squares", "2026-08-01", "Draw Punnett squares for pea plants", "Short quiz", "Whiteboard", "Students understand ratios"],
        ["DNA Structure", "Double Helix model", "2026-08-02", "Build DNA model using clay", "Peer grading", "Clay models", "Students know base pairs"],
    ])

    files = {"file": ("test.xlsx", excel_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    
    resp = await client.post(
        f"/school/subjects/{subject.id}/learning-plans/import",
        files=files,
        headers={"Authorization": school_admin_token}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["topic_name"] == "Mendelian Genetics"
    assert data[0]["sub_topic"] == "Punnett Squares"
    assert data[0]["date"] == "2026-08-01"
    assert data[1]["topic_name"] == "DNA Structure"
    assert data[1]["sub_topic"] == "Double Helix model"

    # Verify db state
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        # Retrieve the auto-created chapter "Mendelian Genetics"
        result_chap = await session.execute(
            select(Chapter).where(Chapter.subject_id == subject.id, Chapter.name == "Mendelian Genetics")
        )
        chapter = result_chap.scalar_one_or_none()
        assert chapter is not None
        
        result_plans = await session.execute(
            select(LearningPlan).where(LearningPlan.chapter_id == chapter.id).order_by(LearningPlan.id.asc())
        )
        plans = result_plans.scalars().all()
        assert len(plans) == 1
        assert plans[0].topic_name == "Mendelian Genetics"
        assert plans[0].sub_topic == "Punnett Squares"


@pytest.mark.asyncio
async def test_import_learning_plans_teacher(client: AsyncClient, teacher_token, subject):
    # Prepare valid excel sheet
    excel_bytes = generate_test_excel([
        ["Topic Name", "Sub Topic", "Date", "Classwork Activities", "Assessment Task", "Teaching Aids", "Learning Outcomes"],
        ["Alkanes", "Nomenclature", "2026-08-05", "Naming exercises", "Homework assignment", "Molecular models", "Students can name alkanes"],
    ])

    files = {"file": ("organic.xlsx", excel_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    
    resp = await client.post(
        f"/teacher/subjects/{subject.id}/learning-plans/import",
        files=files,
        headers={"Authorization": teacher_token}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["topic_name"] == "Alkanes"
    assert data[0]["sub_topic"] == "Nomenclature"
    assert data[0]["date"] == "2026-08-05"


@pytest.mark.asyncio
async def test_import_learning_plans_validation_errors(client: AsyncClient, school_admin_token, subject):
    # 1. Missing required column (Topic Name)
    excel_bytes_missing_col = generate_test_excel([
        ["Sub Topic", "Date", "Classwork Activities"],
        ["Punnett Squares", "2026-08-01", "Draw Punnett squares for pea plants"],
    ])
    files = {"file": ("missing_col.xlsx", excel_bytes_missing_col, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    resp = await client.post(
        f"/school/subjects/{subject.id}/learning-plans/import",
        files=files,
        headers={"Authorization": school_admin_token}
    )
    assert resp.status_code == 400
    assert "missing required columns" in resp.json()["detail"].lower()

    # 2. Missing required value in row (Topic Name empty in row 2)
    excel_bytes_missing_val = generate_test_excel([
        ["Topic Name", "Sub Topic", "Date", "Classwork Activities"],
        ["", "Punnett Squares", "2026-08-01", "Draw Punnett squares for pea plants"],
    ])
    files = {"file": ("missing_val.xlsx", excel_bytes_missing_val, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    resp = await client.post(
        f"/school/subjects/{subject.id}/learning-plans/import",
        files=files,
        headers={"Authorization": school_admin_token}
    )
    assert resp.status_code == 400
    assert "missing required field" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_import_learning_plans_with_chapter_and_lecture_headers(client: AsyncClient, school_admin_token, subject):
    excel_bytes = generate_test_excel([
        ["Chapter Name", "Lecture Title", "Date", "Classwork Activities", "Assessment Task", "Teaching Aids", "Learning Outcomes"],
        ["Cell Biology", "Mitochondria & Respiration", "2026-08-10", "ATP cycle explanation", "Pop quiz", "Diagrams", "Know ATP function"],
    ])

    files = {"file": ("biology.xlsx", excel_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    
    resp = await client.post(
        f"/school/subjects/{subject.id}/learning-plans/import",
        files=files,
        headers={"Authorization": school_admin_token}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["topic_name"] == "Cell Biology"
    assert data[0]["sub_topic"] == "Mitochondria & Respiration"
    assert data[0]["date"] == "2026-08-10"
