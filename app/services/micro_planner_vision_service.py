import os
import io
import logging
from pydantic import BaseModel, Field
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from google import genai
from google.genai import types

from app.config import settings
from app.models.chapter import Chapter
from app.models.subject import Subject
from app.models.learning_plan import LearningPlan
from app.schemas.learning_plan import LearningPlanOut

logger = logging.getLogger(__name__)


class MicroPlannerRow(BaseModel):
    date: str | None = Field(default=None, description="Date or Day e.g. 21/07/25")
    topic_name: str | None = Field(default=None, description="Topic Name e.g. Reflection and Refraction of Light")
    sub_topic: str = Field(description="Sub Topic or Lecture Title e.g. Numericals from Mirror and Lens Problem")
    classwork_activities: str | None = Field(default=None, description="Classwork, Key strategies, Activities to be done")
    assessment_task: str | None = Field(default=None, description="Assessment / Extension Task / Homework")
    teaching_aids: str | None = Field(default=None, description="Teaching Aids e.g. Digital Board, convex lens")
    learning_outcomes: str | None = Field(default=None, description="Learning Outcomes e.g. Students will learn how to use lens formula")
    remarks: str | None = Field(default=None, description="Remarks e.g. To be continued, completed")


class MicroPlannerExtraction(BaseModel):
    subject: str | None = Field(default=None, description="Subject from header if present")
    month: str | None = Field(default=None, description="Month from header if present")
    rows: list[MicroPlannerRow] = Field(description="Extracted table rows")


async def parse_and_create_micro_planner_entries(
    db: AsyncSession,
    chapter_id: int,
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
) -> list[LearningPlanOut]:
    """
    Accepts an image of a Micro Planner handwritten/printed sheet for a specific Chapter,
    calls Gemini Vision to extract table rows, and creates or updates LearningPlan database records (deduplicated).
    """
    chapter = await db.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    api_key = os.environ.get("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", None)
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY is not configured.")

    client = genai.Client(api_key=api_key)

    prompt = (
        "You are an expert educational curriculum AI assistant specialized in OCR and reading handwritten/printed Micro Planner sheets.\n"
        "Examine the uploaded image of the Micro Planner table carefully.\n"
        "Extract all rows from the table accurately, mapping the columns:\n"
        "- Date / Day -> date\n"
        "- Topic Name -> topic_name\n"
        "- Sub Topic -> sub_topic (Required. If missing or blank, use the topic_name or activity title)\n"
        "- Classwork, Key strategies, Activities to be done -> classwork_activities\n"
        "- Assessment / Extension Task -> assessment_task\n"
        "- Teaching Aids -> teaching_aids\n"
        "- Learning Outcomes -> learning_outcomes\n"
        "- Remarks -> remarks\n\n"
        "If cells contain ditto marks ('\"') or are blank under Topic Name, inherit the Topic Name from the row above.\n"
        "Return all extracted rows strictly matching the JSON schema."
    )

    contents = [
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        prompt,
    ]

    model_name = "gemini-2.5-flash"
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=MicroPlannerExtraction,
            ),
        )
    except Exception as e:
        logger.warning(f"Error calling {model_name}: {e}. Retrying with gemini-2.0-flash...")
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=MicroPlannerExtraction,
            ),
        )

    data = MicroPlannerExtraction.model_validate_json(response.text)

    # Fetch existing LearningPlan records for this chapter to prevent duplicate rows
    stmt = select(LearningPlan).where(LearningPlan.chapter_id == chapter_id)
    res = await db.execute(stmt)
    existing_plans = {p.sub_topic.lower().strip(): p for p in res.scalars().all()}

    processed_plans: list[LearningPlan] = []
    default_topic = chapter.name

    for row in data.rows:
        topic = (row.topic_name or "").strip() or default_topic
        sub_topic = (row.sub_topic or "").strip() or "Lecture Entry"
        sub_key = sub_topic.lower().strip()

        date_val = (row.date or "").strip() or None
        cw_val = (row.classwork_activities or "").strip() or None
        ass_val = (row.assessment_task or "").strip() or None
        aids_val = (row.teaching_aids or "").strip() or None
        outcomes_val = (row.learning_outcomes or "").strip() or None

        if sub_key in existing_plans:
            plan = existing_plans[sub_key]
            if date_val: plan.date = date_val
            if topic: plan.topic_name = topic
            if cw_val: plan.classwork_activities = cw_val
            if ass_val: plan.assessment_task = ass_val
            if aids_val: plan.teaching_aids = aids_val
            if outcomes_val: plan.learning_outcomes = outcomes_val
        else:
            plan = LearningPlan(
                chapter_id=chapter_id,
                date=date_val,
                topic_name=topic,
                sub_topic=sub_topic,
                classwork_activities=cw_val,
                assessment_task=ass_val,
                teaching_aids=aids_val,
                learning_outcomes=outcomes_val,
            )
            db.add(plan)
            existing_plans[sub_key] = plan

        processed_plans.append(plan)

    await db.commit()

    for plan in processed_plans:
        await db.refresh(plan)

    return [LearningPlanOut.model_validate(p) for p in processed_plans]


async def parse_and_create_subject_micro_planner_entries(
    db: AsyncSession,
    subject_id: int,
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
) -> list[LearningPlanOut]:
    """
    Accepts an image of a Micro Planner handwritten/printed sheet for a Subject.
    Extracts rows via Gemini Vision, auto-creates Chapters for unique Topic Names if missing,
    and creates or updates LearningPlan database records (deduplicated).
    """
    subject = await db.get(Subject, subject_id)
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    api_key = os.environ.get("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", None)
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY is not configured.")

    client = genai.Client(api_key=api_key)

    prompt = (
        "You are an expert educational curriculum AI assistant specialized in OCR and reading handwritten/printed Micro Planner sheets.\n"
        "Examine the uploaded image of the Micro Planner table carefully.\n"
        "Extract all rows from the table accurately, mapping the columns:\n"
        "- Date / Day -> date\n"
        "- Topic Name -> topic_name (Acts as the Chapter Name, e.g. 'Reflection and Refraction of Light')\n"
        "- Sub Topic -> sub_topic (Required. If missing or blank, use the topic_name or activity title)\n"
        "- Classwork, Key strategies, Activities to be done -> classwork_activities\n"
        "- Assessment / Extension Task -> assessment_task\n"
        "- Teaching Aids -> teaching_aids\n"
        "- Learning Outcomes -> learning_outcomes\n"
        "- Remarks -> remarks\n\n"
        "If cells contain ditto marks ('\"') or are blank under Topic Name, inherit the Topic Name from the row above.\n"
        "Return all extracted rows strictly matching the JSON schema."
    )

    contents = [
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        prompt,
    ]

    model_name = "gemini-2.5-flash"
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=MicroPlannerExtraction,
            ),
        )
    except Exception as e:
        logger.warning(f"Error calling {model_name}: {e}. Retrying with gemini-2.0-flash...")
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=MicroPlannerExtraction,
            ),
        )

    data = MicroPlannerExtraction.model_validate_json(response.text)

    # Fetch existing chapters for this subject
    stmt = select(Chapter).where(Chapter.subject_id == subject_id)
    res = await db.execute(stmt)
    existing_chapters = {c.name.lower().strip(): c for c in res.scalars().all()}

    # Fetch existing plans across all chapters in this subject to prevent duplicate rows
    chapter_ids = [c.id for c in existing_chapters.values()]
    existing_plans_map: dict[int, dict[str, LearningPlan]] = {}
    if chapter_ids:
        lp_stmt = select(LearningPlan).where(LearningPlan.chapter_id.in_(chapter_ids))
        lp_res = await db.execute(lp_stmt)
        for plan in lp_res.scalars().all():
            if plan.chapter_id not in existing_plans_map:
                existing_plans_map[plan.chapter_id] = {}
            existing_plans_map[plan.chapter_id][plan.sub_topic.lower().strip()] = plan

    processed_plans: list[LearningPlan] = []
    last_topic_name = "General Curriculum"

    for row in data.rows:
        topic_name = (row.topic_name or "").strip() or last_topic_name
        last_topic_name = topic_name
        sub_topic = (row.sub_topic or "").strip() or "Lecture Entry"
        sub_key = sub_topic.lower().strip()

        chapter_key = topic_name.lower().strip()
        if chapter_key in existing_chapters:
            chapter = existing_chapters[chapter_key]
        else:
            chapter = Chapter(subject_id=subject_id, name=topic_name, description=None)
            db.add(chapter)
            await db.flush()
            await db.refresh(chapter)
            existing_chapters[chapter_key] = chapter
            existing_plans_map[chapter.id] = {}

        date_val = (row.date or "").strip() or None
        cw_val = (row.classwork_activities or "").strip() or None
        ass_val = (row.assessment_task or "").strip() or None
        aids_val = (row.teaching_aids or "").strip() or None
        outcomes_val = (row.learning_outcomes or "").strip() or None

        ch_plans = existing_plans_map.setdefault(chapter.id, {})
        if sub_key in ch_plans:
            plan = ch_plans[sub_key]
            if date_val: plan.date = date_val
            if topic_name: plan.topic_name = topic_name
            if cw_val: plan.classwork_activities = cw_val
            if ass_val: plan.assessment_task = ass_val
            if aids_val: plan.teaching_aids = aids_val
            if outcomes_val: plan.learning_outcomes = outcomes_val
        else:
            plan = LearningPlan(
                chapter_id=chapter.id,
                date=date_val,
                topic_name=topic_name,
                sub_topic=sub_topic,
                classwork_activities=cw_val,
                assessment_task=ass_val,
                teaching_aids=aids_val,
                learning_outcomes=outcomes_val,
            )
            db.add(plan)
            ch_plans[sub_key] = plan

        processed_plans.append(plan)

    await db.commit()

    for plan in processed_plans:
        await db.refresh(plan)

    return [LearningPlanOut.model_validate(p) for p in processed_plans]


async def import_subject_learning_plans_excel(
    db: AsyncSession,
    subject_id: int,
    file_bytes: bytes,
) -> list[LearningPlanOut]:
    """
    Imports learning plans from an Excel file, deduplicating by sub_topic per chapter.
    """
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheet = wb.active

    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []

    header = [str(cell).strip().lower() if cell is not None else "" for cell in rows[0]]
    
    col_map = {}
    for idx, col_name in enumerate(header):
        if "topic" in col_name and "sub" not in col_name:
            col_map["topic_name"] = idx
        elif "sub" in col_name:
            col_map["sub_topic"] = idx
        elif "date" in col_name:
            col_map["date"] = idx
        elif "classwork" in col_name or "activit" in col_name:
            col_map["classwork_activities"] = idx
        elif "assess" in col_name or "task" in col_name:
            col_map["assessment_task"] = idx
        elif "aid" in col_name:
            col_map["teaching_aids"] = idx
        elif "outcome" in col_name:
            col_map["learning_outcomes"] = idx

    stmt = select(Chapter).where(Chapter.subject_id == subject_id)
    res = await db.execute(stmt)
    existing_chapters = {c.name.lower().strip(): c for c in res.scalars().all()}

    chapter_ids = [c.id for c in existing_chapters.values()]
    existing_plans_map: dict[int, dict[str, LearningPlan]] = {}
    if chapter_ids:
        lp_stmt = select(LearningPlan).where(LearningPlan.chapter_id.in_(chapter_ids))
        lp_res = await db.execute(lp_stmt)
        for plan in lp_res.scalars().all():
            if plan.chapter_id not in existing_plans_map:
                existing_plans_map[plan.chapter_id] = {}
            existing_plans_map[plan.chapter_id][plan.sub_topic.lower().strip()] = plan

    processed_plans: list[LearningPlan] = []
    last_topic_name = "General Curriculum"

    for r in rows[1:]:
        if not any(r):
            continue
        
        raw_topic = r[col_map["topic_name"]] if "topic_name" in col_map and col_map["topic_name"] < len(r) else None
        raw_sub = r[col_map["sub_topic"]] if "sub_topic" in col_map and col_map["sub_topic"] < len(r) else None
        
        topic_name = str(raw_topic).strip() if raw_topic is not None else last_topic_name
        if not topic_name:
            topic_name = last_topic_name
        last_topic_name = topic_name

        sub_topic = str(raw_sub).strip() if raw_sub is not None else "Lecture Entry"
        if not sub_topic:
            continue
        sub_key = sub_topic.lower().strip()

        chapter_key = topic_name.lower().strip()
        if chapter_key in existing_chapters:
            chapter = existing_chapters[chapter_key]
        else:
            chapter = Chapter(subject_id=subject_id, name=topic_name, description=None)
            db.add(chapter)
            await db.flush()
            await db.refresh(chapter)
            existing_chapters[chapter_key] = chapter
            existing_plans_map[chapter.id] = {}

        get_val = lambda key: str(r[col_map[key]]).strip() if key in col_map and col_map[key] < len(r) and r[col_map[key]] is not None else None

        ch_plans = existing_plans_map.setdefault(chapter.id, {})
        if sub_key in ch_plans:
            plan = ch_plans[sub_key]
            if get_val("date"): plan.date = get_val("date")
            if topic_name: plan.topic_name = topic_name
            if get_val("classwork_activities"): plan.classwork_activities = get_val("classwork_activities")
            if get_val("assessment_task"): plan.assessment_task = get_val("assessment_task")
            if get_val("teaching_aids"): plan.teaching_aids = get_val("teaching_aids")
            if get_val("learning_outcomes"): plan.learning_outcomes = get_val("learning_outcomes")
        else:
            plan = LearningPlan(
                chapter_id=chapter.id,
                date=get_val("date"),
                topic_name=topic_name,
                sub_topic=sub_topic,
                classwork_activities=get_val("classwork_activities"),
                assessment_task=get_val("assessment_task"),
                teaching_aids=get_val("teaching_aids"),
                learning_outcomes=get_val("learning_outcomes"),
            )
            db.add(plan)
            ch_plans[sub_key] = plan

        processed_plans.append(plan)

    await db.commit()

    for plan in processed_plans:
        await db.refresh(plan)

    return [LearningPlanOut.model_validate(p) for p in processed_plans]
