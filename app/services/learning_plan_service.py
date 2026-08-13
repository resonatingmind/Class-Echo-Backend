from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chapter import Chapter
from app.models.learning_plan import LearningPlan
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

    sub_key = payload.sub_topic.strip().lower()
    stmt = select(LearningPlan).where(
        LearningPlan.chapter_id == chapter_id,
        func.lower(LearningPlan.sub_topic) == sub_key,
    )
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()

    if existing:
        if payload.date: existing.date = payload.date
        if payload.topic_name: existing.topic_name = payload.topic_name.strip()
        if payload.classwork_activities: existing.classwork_activities = payload.classwork_activities
        if payload.assessment_task: existing.assessment_task = payload.assessment_task
        if payload.teaching_aids: existing.teaching_aids = payload.teaching_aids
        if payload.learning_outcomes: existing.learning_outcomes = payload.learning_outcomes
        await db.commit()
        await db.refresh(existing)
        return LearningPlanOut.model_validate(existing)

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

    await db.delete(plan)
    await db.commit()
