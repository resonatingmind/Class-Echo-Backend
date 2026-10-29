"""My Growth: a teacher's trends, compared only with their own past.

Rules from the roadmap:
- never against colleagues;
- only reports scored under the current rubric (older ones are counted, not compared);
- rolling averages over a window of lectures, never single-lecture swings;
- lectures the teacher excluded (disputed as not fitting the class type) are left out.
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.llm_report import LLMReport
from app.models.recording import Recording, RecordingStatus
from app.schemas.growth import AreaTrendOut, FocusProgressOut, GrowthOut, GrowthPoint
from app.schemas.recording import FeedbackReason
from app.services.llm_pipeline.config import RUBRIC_VERSION, SCORE_WEIGHTS

# Last WINDOW lectures against the WINDOW before them.
WINDOW = 5
# A window needs at least this many lectures before it is compared.
MIN_PER_WINDOW = 3
# A change smaller than this (points out of 100) is steady, not a trend.
STEADY_WITHIN = 5.0
# Lectures shown in each area's sparkline.
SERIES_LENGTH = 10
# Focus progress looks at this many recent lectures.
FOCUS_RECENT = 4


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _area_value(report: LLMReport, area: str) -> float | None:
    """The area's score as a share of its maximum (0-100); the total for "overall"."""
    if area == "overall":
        return float(report.overall_score) if report.overall_score is not None else None
    entry = (report.score_breakdown or {}).get(area)
    score = entry.get("score") if isinstance(entry, dict) else entry
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return None
    return round(score / SCORE_WEIGHTS[area] * 100, 1)


def _trend(area: str, rows: list[tuple[Recording, LLMReport]]) -> AreaTrendOut:
    points = [
        GrowthPoint(recording_id=rec.id, recorded_at=rec.uploaded_at, value=v)
        for rec, rep in rows
        if (v := _area_value(rep, area)) is not None
    ]
    values = [p.value for p in points]
    recent = values[-WINDOW:]
    previous = values[-2 * WINDOW:-WINDOW]
    out = AreaTrendOut(area=area, recent_avg=_avg(recent), series=points[-SERIES_LENGTH:])
    if len(recent) >= MIN_PER_WINDOW and len(previous) >= MIN_PER_WINDOW:
        out.previous_avg = _avg(previous)
        out.change = round(out.recent_avg - out.previous_avg, 1)
        out.direction = (
            "improving" if out.change >= STEADY_WITHIN
            else "declining" if out.change <= -STEADY_WITHIN
            else "steady"
        )
    return out


def _focus(rows: list[tuple[Recording, LLMReport]]) -> FocusProgressOut | None:
    """The latest focus, and how the teacher's last lectures did in that area
    against their average in the lectures before."""
    for rec, rep in reversed(rows):
        improvements = (rep.raw_llm_response or {}).get("improvements") or []
        focus = next((i for i in improvements if isinstance(i, dict) and i.get("is_focus")), None)
        if focus and focus.get("try_this"):
            break
    else:
        return None
    out = FocusProgressOut(area=focus.get("area"), try_this=str(focus["try_this"]), recording_id=rec.id)
    if out.area not in SCORE_WEIGHTS:
        return out
    values = [v for _, r in rows if (v := _area_value(r, out.area)) is not None]
    recent, earlier = values[-FOCUS_RECENT:], values[-FOCUS_RECENT - WINDOW:-FOCUS_RECENT]
    if len(recent) >= 2 and len(earlier) >= 2:
        baseline = sum(earlier) / len(earlier)
        out.recent = len(recent)
        out.improved = sum(1 for v in recent if v > baseline)
    return out


async def build_growth(db: AsyncSession, teacher_id: int) -> GrowthOut:
    result = await db.execute(
        select(Recording, LLMReport)
        .join(LLMReport, LLMReport.recording_id == Recording.id)
        .where(
            Recording.teacher_id == teacher_id,
            Recording.status == RecordingStatus.completed,
            Recording.is_archived.is_(False),
        )
        .order_by(Recording.uploaded_at, Recording.id)
    )
    all_rows = [(rec, rep) for rec, rep in result.all()]
    current = [(rec, rep) for rec, rep in all_rows if rep.rubric_version == RUBRIC_VERSION]
    rows = [(rec, rep) for rec, rep in current if not rep.excluded_from_trends]

    return GrowthOut(
        rubric_version=RUBRIC_VERSION,
        window=WINDOW,
        lecture_count=len(rows),
        older_rubric_count=len(all_rows) - len(current),
        excluded_count=len(current) - len(rows),
        overall=_trend("overall", rows),
        areas=[_trend(area, rows) for area in SCORE_WEIGHTS],
        focus=_focus(rows),
    )


async def set_excluded(db: AsyncSession, recording_id: int, teacher_id: int, excluded: bool) -> LLMReport:
    result = await db.execute(
        select(LLMReport)
        .join(Recording, Recording.id == LLMReport.recording_id)
        .options(selectinload(LLMReport.feedback))
        .where(Recording.id == recording_id, Recording.teacher_id == teacher_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if excluded and not any(
        fb.verdict == "disagree" and FeedbackReason.class_type.value in (fb.reasons or [])
        for fb in report.feedback
    ):
        raise HTTPException(
            status_code=409,
            detail="Only a lecture you marked as not fitting this type of class can be left out of trends.",
        )
    report.excluded_from_trends = excluded
    await db.commit()
    return report
