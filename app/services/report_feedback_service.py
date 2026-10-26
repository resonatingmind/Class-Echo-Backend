"""Teacher 👍/👎 on report findings, and the disagreement summary used to tune the rubric."""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_report import LLMReport
from app.models.recording import Recording
from app.models.report_feedback import FeedbackTarget, FeedbackVerdict, ReportFeedback
from app.models.school import School
from app.models.teacher import Teacher
from app.schemas.recording import LLMReportOut, ReportFeedbackIn
from app.services.llm_pipeline.config import SCORE_WEIGHTS

# A dimension this many teachers disagree with is the rubric's fault, not theirs.
DISAGREEMENT_ALERT_RATE = 0.30


class FeedbackTargetError(ValueError):
    pass


def resolve_area(report: LLMReport, target_type: FeedbackTarget, target_key: str) -> str | None:
    """Check the target exists on this report and return the dimension it belongs to."""
    if target_type == FeedbackTarget.dimension:
        if target_key not in SCORE_WEIGHTS or target_key not in (report.score_breakdown or {}):
            raise FeedbackTargetError(f"Unknown dimension: {target_key}")
        return target_key

    # Strengths and improvements are addressed by position, using the same
    # derived lists the report screen shows (legacy reports included).
    findings = LLMReportOut.model_validate(report)
    items = findings.strengths if target_type == FeedbackTarget.strength else findings.improvements
    if not target_key.isdigit() or int(target_key) >= len(items):
        raise FeedbackTargetError(f"Unknown {target_type.value}: {target_key}")
    return items[int(target_key)].area


async def list_feedback(db: AsyncSession, report_id: int) -> list[ReportFeedback]:
    result = await db.execute(
        select(ReportFeedback).where(ReportFeedback.report_id == report_id).order_by(ReportFeedback.id)
    )
    return list(result.scalars())


async def upsert_feedback(
    db: AsyncSession, report: LLMReport, teacher_id: int, body: ReportFeedbackIn,
) -> list[ReportFeedback]:
    area = resolve_area(report, body.target_type, body.target_key)
    # Reasons and a comment only mean something on a disagreement.
    disagree = body.verdict == FeedbackVerdict.disagree
    reasons = sorted({r.value for r in body.reasons}) if disagree else []
    comment = (body.comment or "").strip() or None if disagree else None

    result = await db.execute(
        select(ReportFeedback).where(
            ReportFeedback.report_id == report.id,
            ReportFeedback.target_type == body.target_type.value,
            ReportFeedback.target_key == body.target_key,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ReportFeedback(
            report_id=report.id,
            teacher_id=teacher_id,
            target_type=body.target_type.value,
            target_key=body.target_key,
        )
        db.add(row)
    row.area = area
    row.verdict = body.verdict.value
    row.reasons = reasons
    row.comment = comment
    await db.commit()
    return await list_feedback(db, report.id)


async def remove_feedback(
    db: AsyncSession, report_id: int, target_type: FeedbackTarget, target_key: str,
) -> list[ReportFeedback]:
    await db.execute(
        delete(ReportFeedback).where(
            ReportFeedback.report_id == report_id,
            ReportFeedback.target_type == target_type.value,
            ReportFeedback.target_key == target_key,
        )
    )
    await db.commit()
    return await list_feedback(db, report_id)


async def disagreement_summary(db: AsyncSession, rubric_version: str | None) -> dict:
    """Disagreement rate per dimension and per reason, across all schools.

    The rate per dimension is teachers who disagreed with a finding in that
    area over teachers who have at least one report under this rubric.
    """
    report_filter = [LLMReport.rubric_version == rubric_version] if rubric_version else []

    teachers_total = (await db.execute(
        select(func.count(func.distinct(Recording.teacher_id)))
        .select_from(LLMReport)
        .join(Recording, Recording.id == LLMReport.recording_id)
        .where(*report_filter)
    )).scalar_one()
    reports_total = (await db.execute(
        select(func.count(LLMReport.id)).where(*report_filter)
    )).scalar_one()

    feedback_q = (
        select(ReportFeedback)
        .join(LLMReport, LLMReport.id == ReportFeedback.report_id)
        .where(*report_filter)
    )
    rows = list((await db.execute(feedback_q)).scalars())

    by_area: dict[str, dict] = {
        area: {"area": area, "agree": 0, "disagree": 0, "teachers": set()} for area in SCORE_WEIGHTS
    }
    by_reason: dict[str, int] = {}
    by_target: dict[str, dict] = {t.value: {"agree": 0, "disagree": 0} for t in FeedbackTarget}
    for row in rows:
        by_target.setdefault(row.target_type, {"agree": 0, "disagree": 0})[row.verdict] += 1
        bucket = by_area.get(row.area) if row.area else None
        if bucket is not None:
            bucket[row.verdict] += 1
            if row.verdict == FeedbackVerdict.disagree.value:
                bucket["teachers"].add(row.teacher_id)
        if row.verdict == FeedbackVerdict.disagree.value:
            for reason in row.reasons or []:
                by_reason[reason] = by_reason.get(reason, 0) + 1

    areas = []
    for bucket in by_area.values():
        teachers = len(bucket.pop("teachers"))
        rate = teachers / teachers_total if teachers_total else 0.0
        areas.append({
            **bucket,
            "teachers_disagreeing": teachers,
            "teacher_rate": round(rate, 3),
            "flagged": teachers_total > 0 and rate > DISAGREEMENT_ALERT_RATE,
        })
    areas.sort(key=lambda a: (-a["teacher_rate"], -a["disagree"]))

    recent = (await db.execute(
        select(ReportFeedback, Recording.id, Recording.lecture_title, Teacher.name, School.name)
        .join(LLMReport, LLMReport.id == ReportFeedback.report_id)
        .join(Recording, Recording.id == LLMReport.recording_id)
        .join(Teacher, Teacher.id == ReportFeedback.teacher_id)
        .outerjoin(School, School.id == Teacher.school_id)
        .where(ReportFeedback.verdict == FeedbackVerdict.disagree.value, *report_filter)
        .order_by(ReportFeedback.updated_at.desc())
        .limit(20)
    )).all()

    return {
        "rubric_version": rubric_version,
        "alert_rate": DISAGREEMENT_ALERT_RATE,
        "reports_total": reports_total,
        "teachers_total": teachers_total,
        "feedback_total": len(rows),
        "disagree_total": sum(1 for r in rows if r.verdict == FeedbackVerdict.disagree.value),
        "by_area": areas,
        "by_reason": sorted(
            ({"reason": k, "count": v} for k, v in by_reason.items()), key=lambda r: -r["count"],
        ),
        "by_target": [{"target_type": k, **v} for k, v in by_target.items()],
        "recent": [
            {
                "recording_id": rec_id,
                "lecture_title": title,
                "teacher_name": teacher_name,
                "school_name": school_name,
                "target_type": fb.target_type,
                "area": fb.area,
                "reasons": fb.reasons or [],
                "comment": fb.comment,
                "updated_at": fb.updated_at,
            }
            for fb, rec_id, title, teacher_name, school_name in recent
        ],
    }
