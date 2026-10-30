"""The next-class brief: what to remember before teaching a timetabled period.

Everything here is read from what the analysis already stored, so the brief
costs no AI call at class time:
- last lesson: the latest completed recording of this class + subject, by any
  teacher (a substitute gets the regular teacher's summary, never their scores)
- today's plan: the planner lecture Record would pre-fill
  (same rules as suggestLecture in the app's lib/lectureContext.ts)
- focus: the primary try-this from this teacher's own latest report
"""
from datetime import date

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.chapter import Chapter
from app.models.class_brief_mute import ClassBriefMute
from app.models.class_brief_open import ClassBriefOpen
from app.models.learning_plan import LearningPlan
from app.models.recording import Recording, RecordingStatus
from app.models.timetable_entry import TimetableEntry
from app.schemas.class_brief import BriefFocusOut, ClassBriefOut, LastLessonOut
from app.schemas.learning_plan import LearningPlanOut


async def get_own_entry(db: AsyncSession, entry_id: int, teacher_id: int) -> TimetableEntry:
    entry = await db.get(TimetableEntry, entry_id)
    if not entry or entry.teacher_id != teacher_id:
        raise HTTPException(status_code=404, detail="Timetable entry not found")
    return entry


def _summary_of(raw: dict | None) -> tuple[list[str], list[str], list[str]]:
    """(covered, unfinished, recall_questions) from a stored report.

    Reports scored before the class summary existed fall back to the
    curriculum coverage every report has, without recall questions.
    """
    raw = raw or {}
    summary = raw.get("class_summary")
    if isinstance(summary, dict) and (summary.get("covered") or summary.get("unfinished")):
        return (
            list(summary.get("covered") or []),
            list(summary.get("unfinished") or []),
            list(summary.get("recall_questions") or []),
        )
    coverage = raw.get("curriculum_coverage") if isinstance(raw.get("curriculum_coverage"), dict) else {}
    return (
        [str(c) for c in coverage.get("covered_concepts") or []][:4],
        [str(c) for c in coverage.get("missing_concepts") or []][:3],
        [],
    )


def _completed_with_report():
    return (
        select(Recording)
        .options(selectinload(Recording.report))
        .where(
            Recording.status == RecordingStatus.completed,
            Recording.is_archived.is_(False),
        )
        .order_by(Recording.uploaded_at.desc(), Recording.id.desc())
    )


async def _last_lesson(db: AsyncSession, subject_id: int, teacher_id: int) -> LastLessonOut | None:
    result = await db.execute(_completed_with_report().where(Recording.subject_id == subject_id).limit(5))
    for rec in result.scalars():
        if not rec.report:
            continue
        covered, unfinished, recall = _summary_of(rec.report.raw_llm_response)
        if not (covered or unfinished):
            continue
        return LastLessonOut(
            recording_id=rec.id,
            title=rec.lecture_title or rec.chapter_name,
            recorded_at=rec.uploaded_at,
            by_other_teacher=rec.teacher_id != teacher_id,
            covered=covered,
            unfinished=unfinished,
            recall_questions=recall,
        )
    return None


async def _focus(db: AsyncSession, teacher_id: int) -> BriefFocusOut | None:
    result = await db.execute(_completed_with_report().where(Recording.teacher_id == teacher_id).limit(5))
    for rec in result.scalars():
        improvements = ((rec.report.raw_llm_response or {}).get("improvements") if rec.report else None) or []
        focus = next(
            (i for i in improvements if isinstance(i, dict) and i.get("is_focus") and i.get("try_this")),
            None,
        )
        if focus:
            return BriefFocusOut(
                recording_id=rec.id,
                area=focus.get("area"),
                observation=str(focus.get("observation", "")),
                try_this=str(focus["try_this"]),
            )
    return None


def _planned_order(plan: LearningPlan) -> tuple:
    """Dated entries first, oldest first; undated ones after, in the order they were planned."""
    return (plan.date is None, plan.date or "", plan.id)


async def _todays_plan(db: AsyncSession, subject_id: int, today: date) -> LearningPlan | None:
    chapters = (
        await db.execute(select(Chapter).where(Chapter.subject_id == subject_id).order_by(Chapter.id))
    ).scalars().all()
    if not chapters:
        return None
    plans = (
        await db.execute(select(LearningPlan).where(LearningPlan.chapter_id.in_([c.id for c in chapters])))
    ).scalars().all()
    recordings = (
        await db.execute(
            select(Recording.chapter_id, Recording.learning_plan_id, Recording.uploaded_at)
            .where(Recording.subject_id == subject_id)
            .order_by(Recording.uploaded_at.desc())
        )
    ).all()
    # The planner has no done flag: a lecture is done once it has a recording.
    recorded = {r.learning_plan_id for r in recordings if r.learning_plan_id is not None}
    open_plans = sorted((p for p in plans if p.id not in recorded), key=_planned_order)

    todays = [p for p in open_plans if p.date == today.isoformat()]
    if todays:
        return todays[0]

    # The next lecture in the chapter taught most recently, then the others.
    latest_chapter = next((r.chapter_id for r in recordings if r.chapter_id is not None), None)
    order = [c.id for c in chapters]
    if latest_chapter in order:
        order.remove(latest_chapter)
        order.insert(0, latest_chapter)
    for chapter_id in order:
        nxt = next((p for p in open_plans if p.chapter_id == chapter_id), None)
        if nxt:
            return nxt
    return None


async def is_muted(db: AsyncSession, teacher_id: int, subject_id: int) -> bool:
    row = await db.execute(
        select(ClassBriefMute.id).where(
            ClassBriefMute.teacher_id == teacher_id, ClassBriefMute.subject_id == subject_id,
        )
    )
    return row.first() is not None


async def build_brief(db: AsyncSession, entry: TimetableEntry, teacher_id: int, today: date) -> ClassBriefOut:
    brief = ClassBriefOut(timetable_entry_id=entry.id, subject_id=entry.subject_id)
    if await is_muted(db, teacher_id, entry.subject_id):
        brief.muted = True
        return brief
    brief.last_lesson = await _last_lesson(db, entry.subject_id, teacher_id)
    plan = await _todays_plan(db, entry.subject_id, today)
    brief.todays_plan = LearningPlanOut.model_validate(plan, from_attributes=True) if plan else None
    brief.focus = await _focus(db, teacher_id)
    return brief


async def record_open(db: AsyncSession, entry: TimetableEntry, teacher_id: int, on: date, source: str) -> None:
    """Count the brief as seen for this class today. Later opens the same day are ignored."""
    await db.execute(
        pg_insert(ClassBriefOpen)
        .values(teacher_id=teacher_id, timetable_entry_id=entry.id, on_date=on, source=source)
        .on_conflict_do_nothing(constraint="uq_class_brief_open")
    )
    await db.commit()


async def set_muted(db: AsyncSession, teacher_id: int, subject_id: int, muted: bool) -> None:
    if muted:
        if not await is_muted(db, teacher_id, subject_id):
            db.add(ClassBriefMute(teacher_id=teacher_id, subject_id=subject_id))
    else:
        await db.execute(
            delete(ClassBriefMute).where(
                ClassBriefMute.teacher_id == teacher_id, ClassBriefMute.subject_id == subject_id,
            )
        )
    await db.commit()
