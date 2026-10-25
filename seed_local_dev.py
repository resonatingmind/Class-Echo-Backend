"""
Seed test users and sample recordings into the LOCAL dev database.

Creates one school with a school admin, a teacher, a class, a subject, a
student, and three recordings for the teacher: one completed with a full AI
report, one processing and one failed, so every state of the report screen
can be checked without uploading audio or spending Gemini quota.

All accounts use the password below. Safe to run more than once; it skips if
the teacher already exists. Refuses to run against anything but the local
docker-compose Postgres.

Usage (from the repo root, with the dev stack up):
    docker compose exec backend python seed_local_dev.py

Accounts:
    School admin  principal@demo.test  /  password123
    Teacher       teacher@demo.test    /  password123
    Student       student@demo.test    /  password123
"""
import asyncio
from datetime import date, datetime, time, timedelta

from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv()

import app.models  # noqa: F401 — register all models
from app.config import settings
from app.core.security import hash_password
from app.database import AsyncSessionLocal
from app.models.chapter import Chapter
from app.models.class_ import Class
from app.models.learning_plan import LearningPlan
from app.models.llm_report import LLMReport
from app.models.recording import Recording, RecordingStatus
from app.models.school import School
from app.models.school_admin import SchoolAdmin
from app.models.student import Student
from app.models.subject import Subject
from app.models.teacher import Teacher
from app.models.timetable_entry import TimetableEntry

PASSWORD = "password123"
LOCAL_DB_HOSTS = ("@postgres:", "@localhost:", "@127.0.0.1:")

# An existing file in uploads/, served by the API at /uploads/...
SAMPLE_AUDIO = "uploads/recordings/1/cee5a1635fbb41fa8e8b7bc52547623a.mp3"
SAMPLE_AUDIO_SECONDS = 668.8

TRANSCRIPT = (
    "Good morning everyone. Today we start a new topic: linear equations in two variables. "
    "Last week we solved equations like 2x + 3 = 7, where there was only one unknown. "
    "Now think about this: a pen and a notebook together cost 50 rupees. Can we find each price? "
    "Take a moment... Riya, what do you think? Right, we cannot, because there are two unknowns. "
    "So we write x + y = 50. This is a linear equation in two variables. "
    "Every pair of values that makes it true is a solution, for example x = 10 and y = 40. "
    "Let us plot a few of these points on the graph... see, they all lie on one straight line. "
    "That is why we call it linear. For homework, write three solutions for 2x + y = 8."
)


def dim(score: int, finding: str, *evidence: str) -> dict:
    return {"score": score, "finding": finding, "evidence": list(evidence)}


SCORE_BREAKDOWN = {
    "verbal_clarity": dim(
        17,
        "Explanations were clear and well paced, with key terms defined before use.",
        '"This is a linear equation in two variables."',
    ),
    "pacing_delivery": dim(
        12,
        "Steady pace overall; the graphing section was slightly rushed.",
        '"Let us plot a few of these points on the graph... see, they all lie on one straight line."',
    ),
    "content_structure": dim(
        13,
        "Lesson moved logically from last week's one-variable equations to the new idea.",
        '"Last week we solved equations like 2x + 3 = 7"',
    ),
    "conceptual_depth": dim(
        15,
        "Good real-life hook, but did not explain why the solutions form a line.",
        '"That is why we call it linear."',
    ),
    "student_engagement": dim(
        7,
        "Only one student was asked a question, and wait time after questions was short.",
        '"Take a moment... Riya, what do you think?"',
        "Most questions were answered by the teacher within a few seconds.",
    ),
    "language_accessibility": dim(
        8,
        "Simple language and a familiar example (pen and notebook prices).",
        '"a pen and a notebook together cost 50 rupees"',
    ),
    "closure_recap": dim(
        2,
        "Ended with homework but no recap of the main idea.",
        '"For homework, write three solutions for 2x + y = 8."',
    ),
}

QUANTITATIVE_METRICS = {
    "wpm_estimate": 132,
    "filler_words_heard": 9,
    "questions_asked": 4,
    "languages_detected": ["English", "Hindi"],
    "code_switching_frequency": "low",
    "top_strengths": [
        "Opened with a relatable real-life problem (pen and notebook prices) that made the need for two variables obvious.",
        "Linked the new topic to last week's one-variable equations before introducing anything new.",
        "Used the graph to show visually why the equation is called linear.",
    ],
    "priority_improvements": [
        "Ask more students, not just one, and wait 3–5 seconds before taking an answer.",
        "End with a one-minute recap: ask a student to state what a solution of x + y = 50 means.",
        "Slow down while plotting points and let students predict the next point.",
    ],
}

RAW_LLM_RESPONSE = {
    "curriculum_coverage": {
        "covered_concepts": [
            "Linear equation in two variables",
            "Solutions of a linear equation",
            "Graph of a linear equation",
        ],
        "missing_concepts": ["Equations of lines parallel to the axes"],
        "coverage_percentage": 75,
    },
    "accuracy_analysis": {
        "correct_explanations": [
            "Correctly stated that a linear equation in two variables has infinitely many solutions.",
        ],
        "potential_inaccuracies": [],
    },
}


async def main():
    if not settings.DATABASE_URL or not any(h in settings.DATABASE_URL for h in LOCAL_DB_HOSTS):
        print("Refusing to seed: DATABASE_URL is not the local dev database.")
        return

    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(Teacher).where(Teacher.email == "teacher@demo.test"))
        if existing.scalar_one_or_none():
            print("Users and recordings already seeded.")
        else:
            await seed_users_and_recordings(session)
        await seed_planner_and_timetable(session)

    print("Password for every account:", PASSWORD)
    print("  School admin  principal@demo.test")
    print("  Teacher       teacher@demo.test")
    print("  Student       student@demo.test")


async def seed_users_and_recordings(session):
    school = School(name="Demo Public School", address="12 Test Lane, Pune")
    session.add(school)
    await session.flush()

    session.add(SchoolAdmin(
        name="Priya Sharma",
        email="principal@demo.test",
        hashed_password=hash_password(PASSWORD),
        school_id=school.id,
        status="active",
        email_verified=True,
    ))

    class_ = Class(name="Grade 9", section="A", school_id=school.id)
    teacher = Teacher(
        name="Anil Mehta",
        email="teacher@demo.test",
        hashed_password=hash_password(PASSWORD),
        school_id=school.id,
        status="active",
        email_verified=True,
        approved_at=datetime.utcnow(),
    )
    session.add_all([class_, teacher])
    await session.flush()

    subject = Subject(
        name="Mathematics",
        class_id=class_.id,
        teacher_id=teacher.id,
        teacher_assigned_at=datetime.utcnow(),
    )
    session.add(subject)
    session.add(Student(
        name="Riya Patel",
        email="student@demo.test",
        hashed_password=hash_password(PASSWORD),
        school_id=school.id,
        class_id=class_.id,
        mobile_number="9000000001",
    ))
    await session.flush()

    now = datetime.utcnow()
    common = dict(
        subject_id=subject.id,
        teacher_id=teacher.id,
        chapter_name="Linear Equations in Two Variables",
        cloudinary_url=SAMPLE_AUDIO,
        cloudinary_public_id=SAMPLE_AUDIO,
        duration_seconds=SAMPLE_AUDIO_SECONDS,
    )

    completed = Recording(
        **common,
        lecture_title="Introduction to linear equations",
        transcription=TRANSCRIPT,
        status=RecordingStatus.completed,
        uploaded_at=now - timedelta(days=1),
        processed_at=now - timedelta(days=1, minutes=-3),
    )
    processing = Recording(
        **common,
        lecture_title="Plotting solutions on a graph",
        status=RecordingStatus.processing,
        processing_step="analysing",
        uploaded_at=now - timedelta(minutes=2),
    )
    failed = Recording(
        **common,
        lecture_title="Lines parallel to the axes",
        status=RecordingStatus.failed,
        error_reason="Too little speech was detected in the recording.",
        uploaded_at=now - timedelta(hours=3),
    )
    session.add_all([completed, processing, failed])
    await session.flush()

    session.add(LLMReport(
        recording_id=completed.id,
        overall_score=float(sum(d["score"] for d in SCORE_BREAKDOWN.values())),
        teaching_quality_notes=(
            "A well-structured introduction that built clearly on last week's lesson and used a "
            "familiar example to motivate two variables. Student participation was limited to one "
            "learner, and the lesson ended without a recap."
        ),
        score_breakdown=SCORE_BREAKDOWN,
        quantitative_metrics=QUANTITATIVE_METRICS,
        raw_llm_response=RAW_LLM_RESPONSE,
    ))
    await session.commit()
    print("Seeded users and recordings.")


# Chapters and planner lectures for Mathematics, dated in days from the seed
# run. The three seeded recordings are linked to the first three lectures; the
# fourth has no recording and is planned for tomorrow, so "Record" on the
# timetable suggests it as today's lecture tomorrow and as the next one after.
CHAPTERS = [
    ("Linear Equations in Two Variables", [
        ("Introduction to linear equations", -2),
        ("Plotting solutions on a graph", -1),
        ("Lines parallel to the axes", None),
        ("Word problems with two variables", 1),
    ]),
    ("Lines and Angles", [
        ("Basic terms and definitions", None),
        ("Pairs of angles", None),
    ]),
]

# (period, start, end, subject) for Monday-Saturday. Science only exists so the
# day has more than one subject in it.
DAY_PLAN = [
    (1, time(8, 0), time(8, 45), "Mathematics"),
    (2, time(9, 0), time(9, 45), "Science"),
    (3, time(10, 0), time(10, 45), "Mathematics"),
    (4, time(11, 30), time(12, 15), "Science"),
    (5, time(13, 0), time(13, 45), "Mathematics"),
]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


async def seed_planner_and_timetable(session):
    teacher = (await session.execute(
        select(Teacher).where(Teacher.email == "teacher@demo.test")
    )).scalar_one()
    maths = (await session.execute(
        select(Subject).where(Subject.teacher_id == teacher.id, Subject.name == "Mathematics")
    )).scalar_one()

    has_chapters = (await session.execute(
        select(Chapter).where(Chapter.subject_id == maths.id)
    )).first()
    if has_chapters:
        print("Planner and timetable already seeded.")
        return

    recordings = {
        r.lecture_title: r
        for r in (await session.execute(
            select(Recording).where(Recording.subject_id == maths.id)
        )).scalars()
    }
    today = date.today()
    for chapter_name, lectures in CHAPTERS:
        chapter = Chapter(subject_id=maths.id, name=chapter_name)
        session.add(chapter)
        await session.flush()
        for sub_topic, day_offset in lectures:
            plan = LearningPlan(
                chapter_id=chapter.id,
                topic_name=chapter_name,
                sub_topic=sub_topic,
                date=(today + timedelta(days=day_offset)).isoformat() if day_offset is not None else None,
            )
            session.add(plan)
            await session.flush()
            rec = recordings.get(sub_topic)
            if rec:
                rec.chapter_id = chapter.id
                rec.chapter_name = chapter_name
                rec.learning_plan_id = plan.id

    science = (await session.execute(
        select(Subject).where(Subject.teacher_id == teacher.id, Subject.name == "Science")
    )).scalar_one_or_none()
    if not science:
        science = Subject(
            name="Science",
            class_id=maths.class_id,
            teacher_id=teacher.id,
            teacher_assigned_at=datetime.utcnow(),
        )
        session.add(science)
        await session.flush()

    subjects = {"Mathematics": maths, "Science": science}
    for day in WEEKDAYS:
        for period, start, end, subject_name in DAY_PLAN:
            session.add(TimetableEntry(
                school_id=teacher.school_id,
                class_id=maths.class_id,
                subject_id=subjects[subject_name].id,
                teacher_id=teacher.id,
                day_of_week=day,
                period_number=period,
                start_time=start,
                end_time=end,
                room="9A",
            ))
    await session.commit()
    print("Seeded chapters, lesson plans, a Science subject and a Monday-Saturday timetable.")


if __name__ == "__main__":
    asyncio.run(main())
