import io
from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chapter import Chapter
from app.models.learning_plan import LearningPlan
from app.models.recording import Recording
from app.schemas.learning_plan import LearningPlanCreate, LearningPlanUpdate, LearningPlanOut


async def list_chapter_learning_plans(
    db: AsyncSession, chapter_id: int
) -> list[LearningPlanOut]:
    result = await db.execute(
        select(LearningPlan)
        .where(LearningPlan.chapter_id == chapter_id)
        .order_by(LearningPlan.id.asc())
    )
    items = result.scalars().all()
    return [LearningPlanOut.model_validate(item) for item in items]


async def create_learning_plan(
    db: AsyncSession, chapter_id: int, payload: LearningPlanCreate
) -> LearningPlanOut:
    chapter = await db.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    plan = LearningPlan(
        chapter_id=chapter_id,
        date=payload.date,
        topic_name=payload.topic_name.strip(),
        sub_topic=payload.sub_topic.strip(),
        classwork_activities=payload.classwork_activities,
        assessment_task=payload.assessment_task,
        teaching_aids=payload.teaching_aids,
        learning_outcomes=payload.learning_outcomes,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return LearningPlanOut.model_validate(plan)


async def update_learning_plan(
    db: AsyncSession, plan_id: int, payload: LearningPlanUpdate
) -> LearningPlanOut:
    plan = await db.get(LearningPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Learning plan item not found")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        if value is not None and isinstance(value, str):
            setattr(plan, field, value.strip())
        else:
            setattr(plan, field, value)

    await db.commit()
    await db.refresh(plan)
    return LearningPlanOut.model_validate(plan)


async def delete_learning_plan(db: AsyncSession, plan_id: int) -> None:
    plan = await db.get(LearningPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Learning plan item not found")

    # Delete all associated recordings first
    result = await db.execute(
        select(Recording).where(Recording.learning_plan_id == plan_id)
    )
    recordings = result.scalars().all()
    for rec in recordings:
        await db.delete(rec)

    await db.delete(plan)
    await db.commit()


SUBJECT_TEMPLATE_HEADERS = [
    "Topic Name",
    "Sub Topic",
    "Date",
    "Classwork Activities",
    "Assessment Task",
    "Teaching Aids",
    "Learning Outcomes"
]


def build_subject_learning_plan_template_workbook() -> io.BytesIO:
    """Builds a blank importable learning plan template at subject level."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Subject Planner"
    ws.append(SUBJECT_TEMPLATE_HEADERS)
    ws.append(
        [
            "Introduction to Chemical Reactions",  # Topic Name (Acts as Chapter Name)
            "Physical vs Chemical Changes",  # Sub Topic
            "2026-08-01",  # Date
            "Class discussion and small experiment with baking soda and vinegar",  # Classwork Activities
            "Short quiz on chemical change indicators",  # Assessment Task
            "Baking soda, vinegar, test tubes, balloons",  # Teaching Aids
            "Students can distinguish physical and chemical changes",  # Learning Outcomes
        ]
    )
    for column_cells in ws.columns:
        length = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = max(15, length + 4)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


async def import_subject_learning_plans_from_excel(
    db: AsyncSession,
    subject_id: int,
    file_bytes: bytes,
) -> list[LearningPlanOut]:
    """Parses learning plans from Excel bytes at subject level, mapping Topic Name directly to Chapter Name."""
    from app.models.subject import Subject
    subject = await db.get(Subject, subject_id)
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    try:
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not read the Excel file: {exc}"
        )

    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows or len(rows) <= 1:
        raise HTTPException(
            status_code=400,
            detail="The sheet is empty or contains no records."
        )

    header = [str(c).strip().lower() if c is not None else "" for c in rows[0]]

    def col(*names: str) -> int | None:
        for name in names:
            if name in header:
                return header.index(name)
        return None

    idx_date = col("date")
    idx_topic = col("topic name", "topic")
    idx_sub_topic = col("sub topic", "sub-topic", "subtopic")
    idx_classwork = col("classwork activities", "classwork")
    idx_assessment = col("assessment task", "assessment")
    idx_teaching_aids = col("teaching aids", "aids")
    idx_outcomes = col("learning outcomes", "outcomes")

    # Topic Name and Sub Topic are required
    if idx_topic is None or idx_sub_topic is None:
        raise HTTPException(
            status_code=400,
            detail="The sheet is missing required columns. Expected at least: 'Topic Name' and 'Sub Topic'."
        )

    # Pre-fetch existing chapters for the subject to avoid duplicates
    existing_chapters_result = await db.execute(
        select(Chapter).where(Chapter.subject_id == subject_id)
    )
    existing_chapters = existing_chapters_result.scalars().all()
    chapters_by_name = {c.name.strip().lower(): c for c in existing_chapters}

    imported_plans = []

    for row_number, row in enumerate(rows[1:], start=2):
        if row is None or all(v is None or str(v).strip() == "" for v in row):
            continue

        topic_name = str(row[idx_topic]).strip() if idx_topic < len(row) and row[idx_topic] is not None else None
        sub_topic = str(row[idx_sub_topic]).strip() if idx_sub_topic < len(row) and row[idx_sub_topic] is not None else None

        if not topic_name or not sub_topic:
            raise HTTPException(
                status_code=400,
                detail=f"Row {row_number} is missing required fields: 'Topic Name' and 'Sub Topic' are required."
            )

        # Topic Name acts directly as the Chapter Name
        chapter_name = topic_name
        chapter_key = chapter_name.lower()
        if chapter_key in chapters_by_name:
            chapter_obj = chapters_by_name[chapter_key]
        else:
            # Create new chapter
            chapter_obj = Chapter(
                subject_id=subject_id,
                name=chapter_name,
                description=None
            )
            db.add(chapter_obj)
            await db.commit()
            await db.refresh(chapter_obj)
            chapters_by_name[chapter_key] = chapter_obj

        date_val = str(row[idx_date]).strip() if (idx_date is not None and idx_date < len(row) and row[idx_date] is not None) else None
        classwork = str(row[idx_classwork]).strip() if (idx_classwork is not None and idx_classwork < len(row) and row[idx_classwork] is not None) else None
        assessment = str(row[idx_assessment]).strip() if (idx_assessment is not None and idx_assessment < len(row) and row[idx_assessment] is not None) else None
        teaching_aids = str(row[idx_teaching_aids]).strip() if (idx_teaching_aids is not None and idx_teaching_aids < len(row) and row[idx_teaching_aids] is not None) else None
        learning_outcomes = str(row[idx_outcomes]).strip() if (idx_outcomes is not None and idx_outcomes < len(row) and row[idx_outcomes] is not None) else None

        plan = LearningPlan(
            chapter_id=chapter_obj.id,
            date=date_val,
            topic_name=topic_name,
            sub_topic=sub_topic,
            classwork_activities=classwork,
            assessment_task=assessment,
            teaching_aids=teaching_aids,
            learning_outcomes=learning_outcomes,
        )
        db.add(plan)
        imported_plans.append(plan)

    if imported_plans:
        await db.commit()
        for p in imported_plans:
            await db.refresh(p)

    return [LearningPlanOut.model_validate(p) for p in imported_plans]
