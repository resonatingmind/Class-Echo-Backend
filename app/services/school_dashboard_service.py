from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.teacher import Teacher
from app.models.student import Student
from app.models.class_ import Class
from app.models.subject import Subject
from app.models.recording import Recording, RecordingStatus
from app.models.llm_report import LLMReport

from app.schemas.school_dashboard import SchoolDashboardOverviewOut
from app.schemas.school_dashboard import (
    SchoolAnalyticsOut,
    TeacherRankingItem,
    TeacherRankingOut,
    RecentActivityItem,
    RecentActivityOut,
)



async def get_school_dashboard_overview(
    db: AsyncSession,
    school_id: int,
) -> SchoolDashboardOverviewOut:

    # -------------------------
    # Teachers
    # -------------------------
    total_teachers = (
        await db.scalar(
            select(func.count(Teacher.id))
            .where(Teacher.school_id == school_id)
        )
    ) or 0

    # -------------------------
    # Students
    # -------------------------
    total_students = (
        await db.scalar(
            select(func.count(Student.id))
            .where(Student.school_id == school_id)
        )
    ) or 0

    # -------------------------
    # Classes
    # -------------------------
    total_classes = (
        await db.scalar(
            select(func.count(Class.id))
            .where(Class.school_id == school_id)
        )
    ) or 0

    # -------------------------
    # Subjects
    # -------------------------
    total_subjects = (
        await db.scalar(
            select(func.count(Subject.id))
            .join(Class)
            .where(Class.school_id == school_id)
        )
    ) or 0

    # -------------------------
    # Recordings
    # -------------------------
    total_recordings = (
        await db.scalar(
            select(func.count(Recording.id))
            .join(Teacher)
            .where(Teacher.school_id == school_id)
        )
    ) or 0

    # -------------------------
    # Pending Reports
    # -------------------------
    pending_reports = (
        await db.scalar(
            select(func.count(Recording.id))
            .join(Teacher)
            .where(
                Teacher.school_id == school_id,
                Recording.status.in_(
                    [
                        RecordingStatus.pending,
                        RecordingStatus.processing,
                    ]
                ),
            )
        )
    ) or 0

    return SchoolDashboardOverviewOut(
        total_teachers=total_teachers,
        total_students=total_students,
        total_classes=total_classes,
        total_subjects=total_subjects,
        total_recordings=total_recordings,
        pending_reports=pending_reports,
    )
    
    
async def get_school_analytics(
    db: AsyncSession,
    school_id: int,
) -> SchoolAnalyticsOut:

    result = await db.execute(
        select(LLMReport)
        .join(Recording, Recording.id == LLMReport.recording_id)
        .join(Teacher, Teacher.id == Recording.teacher_id)
        .where(Teacher.school_id == school_id)
    )

    reports = result.scalars().all()

    if not reports:
        return SchoolAnalyticsOut(
            overall_score=0,
            engagement_score=0,
            confidence_score=0,
            clarity_score=0,
            fluency_score=0,
            pace_score=0,
            curriculum_coverage=0,
            accuracy_score=0,
        )

    def average(values):
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values), 2) if values else 0

    overall = average([
        r.overall_score
        for r in reports
    ])

    engagement = average([
        (r.score_breakdown or {}).get("student_engagement", {}).get("score")
        for r in reports
    ])

    # Your current pipeline doesn't produce a separate confidence score.
    # Use language accessibility until confidence is added to the LLM output.
    confidence = average([
        (r.score_breakdown or {}).get("language_accessibility", {}).get("score")
        for r in reports
    ])

    clarity = average([
        (r.score_breakdown or {}).get("verbal_clarity", {}).get("score")
        for r in reports
    ])

    # Use conceptual depth as the fluency metric for now.
    fluency = average([
        (r.score_breakdown or {}).get("conceptual_depth", {}).get("score")
        for r in reports
    ])

    pace = average([
        (r.score_breakdown or {}).get("pacing_delivery", {}).get("score")
        for r in reports
    ])

    coverage = average([
        (r.raw_llm_response or {})
        .get("curriculum_coverage", {})
        .get("coverage_percentage")
        for r in reports
    ])

    accuracy_list = []

    for report in reports:
        raw = report.raw_llm_response or {}

        analysis = raw.get("accuracy_analysis")

        if not analysis:
            continue

        correct = len(
            analysis.get("correct_explanations", [])
        )

        wrong = len(
            analysis.get("potential_inaccuracies", [])
        )

        total = correct + wrong

        if total > 0:
            accuracy_list.append(
                (correct / total) * 100
            )

    accuracy = average(accuracy_list)

    return SchoolAnalyticsOut(
        overall_score=overall,
        engagement_score=engagement,
        confidence_score=confidence,
        clarity_score=clarity,
        fluency_score=fluency,
        pace_score=pace,
        curriculum_coverage=coverage,
        accuracy_score=accuracy,
    )
    

async def get_teacher_ranking(
    db: AsyncSession,
    school_id: int,
) -> TeacherRankingOut:

    result = await db.execute(
        select(Teacher)
        .where(Teacher.school_id == school_id)
    )

    teachers = result.scalars().all()

    ranking: list[TeacherRankingItem] = []

    for teacher in teachers:

        recordings_result = await db.execute(
            select(Recording)
            .options(
                selectinload(Recording.report)
            )
            .where(
                Recording.teacher_id == teacher.id
            )
        )

        recordings = recordings_result.scalars().all()

        scores = []

        for recording in recordings:

            if recording.report and recording.report.overall_score is not None:
                scores.append(recording.report.overall_score)

        overall = (
            round(sum(scores) / len(scores), 2)
            if scores
            else 0
        )

        ranking.append(
            TeacherRankingItem(
                teacher_id=teacher.id,
                teacher_name=teacher.name,
                total_lectures=len(recordings),
                overall_score=overall,
            )
        )

    ranking.sort(
        key=lambda x: x.overall_score,
        reverse=True,
    )

    return TeacherRankingOut(
        teachers=ranking,
    )
    


async def get_recent_activity(
    db: AsyncSession,
    school_id: int,
) -> RecentActivityOut:

    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.teacher),
            selectinload(Recording.chapter),
        )
        .join(Teacher)
        .where(
            Teacher.school_id == school_id
        )
        .order_by(
            Recording.uploaded_at.desc()
        )
        .limit(10)
    )

    recordings = result.scalars().all()

    activities = []

    for recording in recordings:

        activities.append(
            RecentActivityItem(
                recording_id=recording.id,
                teacher_name=recording.teacher.name,
                lecture_title=recording.lecture_title,
                chapter_name=(
                    recording.chapter.name
                    if recording.chapter
                    else None
                ),
                uploaded_at=recording.uploaded_at,
                status=recording.status.value,
            )
        )

    return RecentActivityOut(
        activities=activities,
    )