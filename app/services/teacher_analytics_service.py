from datetime import datetime
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.subject import Subject
from app.models.class_ import Class
from app.models.teacher import Teacher
from app.models.recording import Recording, RecordingStatus
from app.models.llm_report import LLMReport
from app.schemas.teacher_analytics import (
    TeacherAnalyticsOut,
    RecentLectureOut,
    SubjectPerformanceOut,
    MonthlyPerformanceOut,
)
from app.services.llm_pipeline.config import SCORE_WEIGHTS


def normalize_score(raw_score: int | float, dimension: str) -> float:
    max_score = SCORE_WEIGHTS.get(dimension, 100)

    if max_score <= 0:
        return 0

    return (raw_score / max_score) * 100


async def get_teacher_analytics(
    db: AsyncSession,
    teacher_id: int,
    class_id: int | None = None,
    subject_id: int | None = None,
    semester: int | None = None,
    status: str | None = None,
    search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> TeacherAnalyticsOut:
    print("SEARCH =", search)
    # -------------------------------
    # Load Teacher
    # -------------------------------
    teacher_result = await db.execute(
        select(Teacher).where(Teacher.id == teacher_id)
    )

    teacher = teacher_result.scalar_one_or_none()

    if teacher is None:
        raise ValueError("Teacher not found")

    # -------------------------------
    # Load Recordings + Reports
    # -------------------------------
    query = (
        select(Recording)
        .options(
            selectinload(Recording.report),
            selectinload(Recording.chapter),
            selectinload(Recording.subject),
        )
        .join(Subject, Recording.subject_id == Subject.id)
        .join(Class, Subject.class_id == Class.id)
        .where(
            Recording.teacher_id == teacher_id,
        )
    )

    # -------------------------
    # Optional Filters
    # -------------------------
    if class_id is not None:
        query = query.where(Class.id == class_id)

    if subject_id is not None:
        query = query.where(Subject.id == subject_id)

    # if semester is not None:
    #     query = query.where(Class.semester == semester)

    if status is not None:
        query = query.where(Recording.status == status)

    if search:
        query = query.where(
            or_(
                Recording.lecture_title.ilike(f"%{search}%"),
                Recording.chapter_name.ilike(f"%{search}%"),
            )
        )

    if start_date is not None:
        query = query.where(Recording.uploaded_at >= start_date)

    if end_date is not None:
        query = query.where(Recording.uploaded_at <= end_date)

    query = query.order_by(Recording.uploaded_at.desc())

    recordings_result = await db.execute(query)
    recordings = recordings_result.scalars().all()

    total_lectures = len(recordings)

    completed_lectures = sum(
        1 for r in recordings if r.status == RecordingStatus.completed
    )

    pending_lectures = sum(
        1 for r in recordings if r.status == RecordingStatus.pending
    )

    failed_lectures = sum(
        1 for r in recordings if r.status == RecordingStatus.failed
    )

    reports = [r.report for r in recordings if r.report is not None]
    
    # -------------------------------
    # Score Statistics
    # -------------------------------
    report_scores = [
        report.overall_score
        for report in reports
        if report.overall_score is not None
    ]

    highest_score = max(report_scores) if report_scores else 0
    lowest_score = min(report_scores) if report_scores else 0

    # -------------------------------
    # Overall Score
    # -------------------------------
    overall_score = 0.0

    if reports:
        overall_score = sum(
            report.overall_score or 0 for report in reports
        ) / len(reports)

    # -------------------------------
    # Curriculum Coverage
    # -------------------------------
    coverage_scores = []

    for report in reports:
        raw = report.raw_llm_response or {}
        cc = raw.get("curriculum_coverage")
        if cc:
            coverage_scores.append(cc.get("coverage_percentage", 0))

    curriculum_coverage = (
        sum(coverage_scores) / len(coverage_scores) if coverage_scores else 0
    )

    # -------------------------------
    # Accuracy Score
    # -------------------------------
    accuracy_scores = []

    for report in reports:
        raw = report.raw_llm_response or {}
        aa = raw.get("accuracy_analysis")
        if aa:
            correct = len(aa.get("correct_explanations", []))
            wrong = len(aa.get("potential_inaccuracies", []))
            total = correct + wrong
            if total > 0:
                accuracy_scores.append((correct / total) * 100)

    accuracy_score = (
        sum(accuracy_scores) / len(accuracy_scores) if accuracy_scores else 100
    )

    # -------------------------------
    # Engagement Score
    # -------------------------------
    engagement_scores = []

    for report in reports:
        breakdown = report.score_breakdown or {}
        engagement = breakdown.get("student_engagement")
        if engagement:
            engagement_scores.append(
                normalize_score(
                    engagement.get("score", 0),
                    "student_engagement",
                )
            )

    engagement_score = (
        sum(engagement_scores) / len(engagement_scores) if engagement_scores else 0
    )
    
    # -------------------------------
    # Confidence Score
    # -------------------------------
    confidence_scores = []

    for report in reports:
        breakdown = report.score_breakdown or {}
        confidence = breakdown.get("language_accessibility")
        if confidence:
            confidence_scores.append(
                normalize_score(
                    confidence.get("score", 0),
                    "language_accessibility",
                )
            )

    confidence_score = (
        sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0
    )

    # -------------------------------
    # Speech Analysis
    # -------------------------------
    clarity_scores = []
    fluency_scores = []
    pace_scores = []

    for report in reports:
        breakdown = report.score_breakdown or {}
        clarity = breakdown.get("verbal_clarity")
        pacing = breakdown.get("pacing_delivery")

        if clarity:
            clarity_scores.append(
                normalize_score(
                    clarity.get("score", 0),
                    "verbal_clarity",
                )
            )

        if pacing:
            pace_scores.append(
                normalize_score(
                    pacing.get("score", 0),
                    "pacing_delivery",
                )
            )
            # Use pacing as fluency until a separate fluency metric exists
            fluency_scores.append(
                normalize_score(
                    pacing.get("score", 0),
                    "pacing_delivery",
                )
            )
            
    clarity_score = sum(clarity_scores) / len(clarity_scores) if clarity_scores else 0
    fluency_score = sum(fluency_scores) / len(fluency_scores) if fluency_scores else 0
    pace_score = sum(pace_scores) / len(pace_scores) if pace_scores else 0
    
    # -------------------------------
    # Average Duration
    # -------------------------------
    durations = [
        r.duration_seconds
        for r in recordings
        if r.duration_seconds is not None
    ]

    average_duration = sum(durations) / len(durations) if durations else 0
    longest_lecture = max(durations) if durations else 0
    shortest_lecture = min(durations) if durations else 0
    total_recording_hours = sum(durations) / 3600 if durations else 0

    # -------------------------------
    # Subject Performance
    # -------------------------------
    subject_stats: dict[int, dict] = {}

    for recording in recordings:
        subject = recording.subject
        if subject is None:
            continue

        if subject.id not in subject_stats:
            subject_stats[subject.id] = {
                "subject_name": subject.name,
                "lectures": 0,
                "score_sum": 0,
                "score_count": 0,
                "coverage_sum": 0,
                "coverage_count": 0,
                "engagement_sum": 0,
                "engagement_count": 0,
                "accuracy_sum": 0,
                "accuracy_count": 0,
            }

        stats = subject_stats[subject.id]
        stats["lectures"] += 1

        report = recording.report
        if report is None:
            continue

        if report.overall_score is not None:
            stats["score_sum"] += report.overall_score
            stats["score_count"] += 1

        raw = report.raw_llm_response or {}
        coverage = raw.get("curriculum_coverage")
        if coverage:
            stats["coverage_sum"] += coverage.get("coverage_percentage", 0)
            stats["coverage_count"] += 1

        breakdown = report.score_breakdown or {}
        engagement = breakdown.get("student_engagement")
        if engagement:
            stats["engagement_sum"] += normalize_score(
                engagement.get("score", 0),
                "student_engagement",
            )
            stats["engagement_count"] += 1

        accuracy = raw.get("accuracy_analysis")
        if accuracy:
            correct = len(accuracy.get("correct_explanations", []))
            wrong = len(accuracy.get("potential_inaccuracies", []))
            total = correct + wrong
            if total > 0:
                stats["accuracy_sum"] += (correct / total) * 100
                stats["accuracy_count"] += 1

    subject_performance = []

    for subject_id, stats in subject_stats.items():
        subject_performance.append(
            SubjectPerformanceOut(
                subject_id=subject_id,
                subject_name=stats["subject_name"],
                lectures=stats["lectures"],
                average_score=round(
                    stats["score_sum"] / max(stats["score_count"], 1),
                    2,
                ),
                curriculum_coverage=round(
                    stats["coverage_sum"] / max(stats["coverage_count"], 1),
                    2,
                ),
                engagement_score=round(
                    stats["engagement_sum"] / max(stats["engagement_count"], 1),
                    2,
                ),
                accuracy_score=round(
                    stats["accuracy_sum"] / max(stats["accuracy_count"], 1),
                    2,
                ),
            )
        )

    subject_performance.sort(key=lambda x: x.average_score, reverse=True)
    
    # -------------------------------
    # Monthly Performance
    # -------------------------------
    monthly_stats: dict[str, dict] = {}

    for recording in recordings:
        month = recording.uploaded_at.strftime("%b %Y")

        if month not in monthly_stats:
            monthly_stats[month] = {
                "lectures": 0,
                "score_sum": 0,
                "score_count": 0,
            }

        monthly_stats[month]["lectures"] += 1

        if recording.report and recording.report.overall_score is not None:
            monthly_stats[month]["score_sum"] += recording.report.overall_score
            monthly_stats[month]["score_count"] += 1

    monthly_performance = []

    for month, stats in monthly_stats.items():
        monthly_performance.append(
            MonthlyPerformanceOut(
                month=month,
                lectures=stats["lectures"],
                average_score=round(
                    stats["score_sum"] / max(stats["score_count"], 1),
                    2,
                ),
            )
        )

    monthly_performance.sort(key=lambda x: x.month)    

    # -------------------------------
    # Recent Lectures
    # -------------------------------
    recent = []

    for recording in recordings[:5]:
        recent.append(
            RecentLectureOut(
                recording_id=recording.id,
                lecture_title=recording.lecture_title,
                chapter_name=recording.chapter.name if recording.chapter else None,
                uploaded_at=recording.uploaded_at,
                overall_score=(
                    recording.report.overall_score
                    if recording.report
                    else None
                ),
                status=recording.status.value,
            )
        )
        
    return TeacherAnalyticsOut(
        teacher_id=teacher.id,
        teacher_name=teacher.name,
        total_lectures=total_lectures,
        completed_lectures=completed_lectures,
        pending_lectures=pending_lectures,
        failed_lectures=failed_lectures,
        overall_score=round(overall_score, 2),
        engagement_score=round(engagement_score, 2),
        confidence_score=round(confidence_score, 2),
        curriculum_coverage=round(curriculum_coverage, 2),
        accuracy_score=round(accuracy_score, 2),
        clarity_score=round(clarity_score, 2),
        fluency_score=round(fluency_score, 2),
        pace_score=round(pace_score, 2),
        average_duration_seconds=round(average_duration, 2),
        highest_score=round(highest_score, 2),
        lowest_score=round(lowest_score, 2),
        longest_lecture_seconds=round(longest_lecture, 2),
        shortest_lecture_seconds=round(shortest_lecture, 2),
        total_recording_hours=round(total_recording_hours, 2),
        recent_lectures=recent,
        subject_performance=subject_performance,
        monthly_performance=monthly_performance,
    )