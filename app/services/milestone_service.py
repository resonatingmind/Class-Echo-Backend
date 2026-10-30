"""Week streaks and milestones: reasons to keep recording, in a grown-up tone.

Streaks count weeks, not days. A week counts when the teacher records at
least the school's streak_min_recordings lectures in it. Weeks in which nobody
at the school recorded anything are taken to be holidays and skipped, so a
break never costs a streak. The current week is still under way, so falling
short in it never breaks a streak either.

Milestones are worked out from the teacher's data each time My Growth loads,
and stored the first time they appear (achieved_at is when it happened in the
data). They are private unless the teacher shares one with their admin.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_report import LLMReport
from app.models.recording import Recording, RecordingStatus
from app.models.school import School
from app.models.self_rating import SelfRating
from app.models.teacher import Teacher
from app.models.teacher_milestone import TeacherMilestone
from app.schemas.growth import GrowthOut, MilestoneOut, StreakOut
from app.services.llm_pipeline.config import RUBRIC_VERSION, SCORE_WEIGHTS

DEFAULT_STREAK_MIN = 2
STREAK_MILESTONE_WEEKS = 4
LECTURES_MILESTONE = 10
SELF_RATINGS_MILESTONE = 5
SELF_MATCH_IN_A_ROW = 3
# best_engagement needs this many earlier lectures to beat, or the first ones would all be bests.
BEST_AFTER = 5
FOCUS_ACHIEVED_IMPROVED = 3

# The four plain-language self-rating areas and the rubric dimensions behind
# each; same mapping as SELF_RATING_AREAS in the app's SelfRating.tsx.
SELF_RATING_DIMENSIONS = {
    "clarity": ("verbal_clarity", "pacing_delivery", "language_accessibility"),
    "engagement": ("student_engagement",),
    "coverage": ("content_structure", "conceptual_depth"),
    "recap": ("closure_recap",),
}


def week_of(moment: datetime | date) -> date:
    day = moment.date() if isinstance(moment, datetime) else moment
    return day - timedelta(days=day.weekday())


@dataclass
class StreakHistory:
    current: int = 0
    best: int = 0
    this_week: int = 0
    threshold: int = DEFAULT_STREAK_MIN
    # When the running streak first reached STREAK_MILESTONE_WEEKS.
    reached_milestone_at: datetime | None = None
    weeks: list[date] = field(default_factory=list)


def streak_history(
    teacher_times: list[datetime], school_weeks: set[date], threshold: int, today: date,
) -> StreakHistory:
    """Walk the school's active weeks in order, counting the teacher's qualifying ones."""
    by_week: dict[date, list[datetime]] = defaultdict(list)
    for t in sorted(teacher_times):
        by_week[week_of(t)].append(t)
    this_week = week_of(today)
    out = StreakHistory(this_week=len(by_week.get(this_week, [])), threshold=threshold)
    running = 0
    for week in sorted((school_weeks | set(by_week)) - {this_week}) + [this_week]:
        if week > this_week:
            break
        times = by_week.get(week, [])
        if len(times) >= threshold:
            running += 1
            if running == STREAK_MILESTONE_WEEKS and out.reached_milestone_at is None:
                out.reached_milestone_at = times[threshold - 1]
        elif week != this_week:
            running = 0
        out.best = max(out.best, running)
    out.current = running
    return out


async def _school_weeks(db: AsyncSession, school_id: int | None, since: datetime) -> set[date]:
    if school_id is None:
        return set()
    times = (await db.execute(
        select(Recording.uploaded_at)
        .join(Teacher, Teacher.id == Recording.teacher_id)
        .where(Teacher.school_id == school_id, Recording.uploaded_at >= since)
    )).scalars()
    return {week_of(t) for t in times}


async def _threshold(db: AsyncSession, school_id: int | None) -> int:
    if school_id is None:
        return DEFAULT_STREAK_MIN
    school = await db.get(School, school_id)
    return school.streak_min_recordings if school else DEFAULT_STREAK_MIN


async def build_streak(
    db: AsyncSession, teacher: Teacher, recorded: list[datetime], today: date | None = None,
) -> StreakHistory:
    today = today or date.today()
    threshold = await _threshold(db, teacher.school_id)
    since = min(recorded) if recorded else datetime.combine(today, datetime.min.time())
    school_weeks = await _school_weeks(db, teacher.school_id, since)
    history = streak_history(recorded, school_weeks, threshold, today)
    return history


def streak_out(history: StreakHistory) -> StreakOut:
    return StreakOut(
        weeks=history.current, best=history.best, threshold=history.threshold, this_week=history.this_week,
    )


def _report_area_rating(breakdown: dict | None, area: str) -> int | None:
    """The report's view of a self-rating area on the teacher's 1-5 scale."""
    score = maximum = 0.0
    for dim in SELF_RATING_DIMENSIONS[area]:
        entry = (breakdown or {}).get(dim)
        value = entry.get("score") if isinstance(entry, dict) else entry
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            score += value
            maximum += SCORE_WEIGHTS[dim]
    return 1 + round(score / maximum * 4) if maximum else None


def _self_rating_matched(rating: SelfRating, breakdown: dict | None) -> bool:
    """Every rated area within one point of the report's view: the app's You spotted this yourself."""
    rated = [(area, mine) for area, mine in (rating.ratings or {}).items() if area in SELF_RATING_DIMENSIONS]
    if not rated:
        return False
    for area, mine in rated:
        theirs = _report_area_rating(breakdown, area)
        if theirs is None or abs(mine - theirs) > 1:
            return False
    return True


def _engagement(report: LLMReport) -> float | None:
    entry = (report.score_breakdown or {}).get("student_engagement")
    value = entry.get("score") if isinstance(entry, dict) else entry
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def found_milestones(
    rows: list[tuple[Recording, LLMReport]],
    growth: GrowthOut,
    ratings: list[tuple[SelfRating, dict | None]],
    streak: StreakHistory,
) -> dict[str, tuple[datetime, dict]]:
    """Every milestone the data shows, as {key: (achieved_at, meta)}.

    rows are all the teacher's completed lectures with a report, oldest first;
    growth is My Growth (current rubric, exclusions applied).
    """
    found: dict[str, tuple[datetime, dict]] = {}
    if rows:
        found["first_recording"] = (rows[0][0].uploaded_at, {})
    if len(rows) >= LECTURES_MILESTONE:
        found["ten_lectures"] = (rows[LECTURES_MILESTONE - 1][0].uploaded_at, {})
    if streak.reached_milestone_at:
        found["streak_4_weeks"] = (streak.reached_milestone_at, {})

    focus = growth.focus
    if focus and focus.area and focus.recent and focus.improved >= FOCUS_ACHIEVED_IMPROVED:
        found[f"focus_achieved:{focus.area}"] = (rows[-1][0].uploaded_at, {"area": focus.area})

    best: tuple[datetime, dict] | None = None
    current = [(rec, rep) for rec, rep in rows if rep.rubric_version == RUBRIC_VERSION and not rep.excluded_from_trends]
    values = [(rec, v) for rec, rep in current if (v := _engagement(rep)) is not None]
    for i in range(BEST_AFTER, len(values)):
        rec, value = values[i]
        if value > max(v for _, v in values[:i]):
            best = (rec.uploaded_at, {"score": value, "max": SCORE_WEIGHTS["student_engagement"]})
    if best:
        found["best_engagement"] = best

    for rec, rep in rows:
        coverage = (rep.raw_llm_response or {}).get("curriculum_coverage") or {}
        if (
            rec.chapter_id and isinstance(coverage, dict)
            and coverage.get("covered_concepts") and not coverage.get("missing_concepts")
        ):
            found.setdefault(
                f"chapter_covered:{rec.chapter_id}",
                (rec.uploaded_at, {"chapter": rec.chapter_name or rec.lecture_title}),
            )

    given = [(r, b) for r, b in ratings if not r.skipped]
    if len(given) >= SELF_RATINGS_MILESTONE:
        found["five_self_ratings"] = (given[SELF_RATINGS_MILESTONE - 1][0].created_at, {})
    in_a_row = 0
    for rating, breakdown in given:
        in_a_row = in_a_row + 1 if _self_rating_matched(rating, breakdown) else 0
        if in_a_row == SELF_MATCH_IN_A_ROW:
            found["self_match_3"] = (rating.created_at, {})
            break
    return found


async def sync_milestones(
    db: AsyncSession,
    teacher_id: int,
    rows: list[tuple[Recording, LLMReport]],
    growth: GrowthOut,
    streak: StreakHistory,
) -> list[TeacherMilestone]:
    """Store newly reached milestones and return all of them, newest first."""
    ratings = (await db.execute(
        select(SelfRating, LLMReport.score_breakdown)
        .join(LLMReport, LLMReport.id == SelfRating.report_id)
        .where(SelfRating.teacher_id == teacher_id)
        .order_by(SelfRating.created_at, SelfRating.id)
    )).all()
    found = found_milestones(rows, growth, [(r, b) for r, b in ratings], streak)

    stored = {
        m.key: m for m in (await db.execute(
            select(TeacherMilestone).where(TeacherMilestone.teacher_id == teacher_id)
        )).scalars()
    }
    changed = False
    for key, (achieved_at, meta) in found.items():
        existing = stored.get(key)
        if existing is None:
            stored[key] = TeacherMilestone(teacher_id=teacher_id, key=key, achieved_at=achieved_at, meta=meta)
            db.add(stored[key])
            changed = True
        elif key == "best_engagement" and achieved_at > existing.achieved_at:
            # A new best moves the milestone forward; sharing stays as the teacher set it.
            existing.achieved_at, existing.meta = achieved_at, meta
            changed = True
    if changed:
        await db.commit()
    return sorted(stored.values(), key=lambda m: m.achieved_at, reverse=True)


def milestone_out(m: TeacherMilestone) -> MilestoneOut:
    return MilestoneOut(id=m.id, key=m.key, achieved_at=m.achieved_at, meta=m.meta or {}, shared=m.shared)


async def set_shared(db: AsyncSession, milestone_id: int, teacher_id: int, shared: bool) -> TeacherMilestone:
    milestone = await db.get(TeacherMilestone, milestone_id)
    if milestone is None or milestone.teacher_id != teacher_id:
        raise HTTPException(status_code=404, detail="Milestone not found")
    milestone.shared = shared
    await db.commit()
    return milestone
