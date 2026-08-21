import logging
import re
from datetime import datetime
from celery_worker import celery_app
from app.services.llm import analyze_recording

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2)
def process_recording(self, recording_id: int, cloudinary_url: str):
    """
    Celery task: analyse a recording with the LLM stub and save the report.
    Uses a synchronous DB session (psycopg2) to avoid async complexity in Celery.
    """
    import os
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.config import settings
    db_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)

    # Downloaded once and reused for both transcription and evaluation; removed
    # in the finally block below.
    tmp_audio: str | None = None

    try:
        with Session() as session:
            from app.models.recording import Recording, RecordingStatus
            from app.models.llm_report import LLMReport

            recording = session.get(Recording, recording_id)
            if not recording:
                return

            recording.status = RecordingStatus.processing
            recording.error_reason = None
            session.commit()

            from app.services.s3_service import sign_url
            from app.services.llm import _download_audio
            from app.services.llm_pipeline.evaluator import transcribe_audio
            from app.services.rag_service import retrieve_chapter_context

            fresh_url = sign_url(recording.cloudinary_url) or cloudinary_url

            reference_context = None
            if recording.chapter_id:
                # If we don't have transcription, transcribe it first
                if not recording.transcription:
                    logger.info(f"Recording {recording_id} is missing transcription. Transcribing audio now...")
                    tmp_audio = _download_audio(fresh_url)
                    transcript = transcribe_audio(tmp_audio)
                    recording.transcription = transcript
                    session.commit()

                # Retrieve chapter context
                if recording.transcription:
                    logger.info(f"Retrieving chapter context for chapter {recording.chapter_id}")
                    reference_context = retrieve_chapter_context(
                        session=session,
                        chapter_id=recording.chapter_id,
                        transcript=recording.transcription
                    )

            if reference_context is None:
                reference_context = {}
            if recording.transcription:
                reference_context["transcript"] = recording.transcription

            if recording.learning_plan_id:
                from app.models.learning_plan import LearningPlan
                lp = session.get(LearningPlan, recording.learning_plan_id)
                if lp:
                    reference_context["learning_plan"] = {
                        "id": lp.id,
                        "date": lp.date,
                        "topic_name": lp.topic_name,
                        "sub_topic": lp.sub_topic,
                        "classwork_activities": lp.classwork_activities,
                        "assessment_task": lp.assessment_task,
                        "teaching_aids": lp.teaching_aids,
                        "learning_outcomes": lp.learning_outcomes,
                    }
            elif recording.chapter_id:
                from app.models.learning_plan import LearningPlan
                plans = session.query(LearningPlan).filter(LearningPlan.chapter_id == recording.chapter_id).all()
                if plans:
                    matched = None
                    if recording.lecture_title:
                        title_clean = recording.lecture_title.strip().lower()
                        for p in plans:
                            if p.sub_topic and p.sub_topic.strip().lower() == title_clean:
                                matched = p
                                break
                            if p.topic_name and p.topic_name.strip().lower() == title_clean:
                                matched = p
                                break
                    if matched:
                        reference_context["learning_plan"] = {
                            "id": matched.id,
                            "date": matched.date,
                            "topic_name": matched.topic_name,
                            "sub_topic": matched.sub_topic,
                            "classwork_activities": matched.classwork_activities,
                            "assessment_task": matched.assessment_task,
                            "teaching_aids": matched.teaching_aids,
                            "learning_outcomes": matched.learning_outcomes,
                        }
                    else:
                        reference_context["chapter_learning_plans"] = [
                            {
                                "id": p.id,
                                "topic_name": p.topic_name,
                                "sub_topic": p.sub_topic,
                                "classwork_activities": p.classwork_activities,
                                "learning_outcomes": p.learning_outcomes,
                            }
                            for p in plans
                        ]

            report_data = analyze_recording(
                fresh_url,
                reference_context=reference_context,
                audio_path=tmp_audio,
            )

            report = session.query(LLMReport).filter_by(recording_id=recording_id).first()
            if report:
                report.overall_score = report_data["overall_score"]
                report.teaching_quality_notes = report_data["teaching_quality_notes"]
                report.score_breakdown = report_data["score_breakdown"]
                report.quantitative_metrics = report_data["quantitative_metrics"]
                report.raw_llm_response = report_data["raw_llm_response"]
            else:
                report = LLMReport(
                    recording_id=recording_id,
                    overall_score=report_data["overall_score"],
                    teaching_quality_notes=report_data["teaching_quality_notes"],
                    score_breakdown=report_data["score_breakdown"],
                    quantitative_metrics=report_data["quantitative_metrics"],
                    raw_llm_response=report_data["raw_llm_response"],
                )
                session.add(report)

            recording.status = RecordingStatus.completed
            recording.error_reason = None
            recording.processed_at = datetime.utcnow()
            session.commit()

    except Exception as exc:
        # Build a user-friendly error reason from the exception
        error_reason = _classify_error(exc)

        # A per-day quota does not reset within any backoff we could sensibly
        # wait out, so retrying only burns the next day's quota and leaves the
        # recording sitting in `processing` for hours. Fail it immediately.
        quota_scope = _quota_scope(exc)
        permanent = _is_permanent(exc)
        is_final_attempt = (
            quota_scope == "daily"
            or permanent
            or self.request.retries >= self.max_retries
        )

        logger.error(
            "Recording %d analysis failed (attempt %d/%d): %s",
            recording_id,
            self.request.retries + 1,
            self.max_retries + 1,
            exc,
            exc_info=True,
        )

        try:
            with Session() as session:
                from app.models.recording import Recording, RecordingStatus
                recording = session.get(Recording, recording_id)
                if recording:
                    if is_final_attempt:
                        recording.status = RecordingStatus.failed
                        recording.error_reason = error_reason
                    else:
                        recording.error_reason = f"Retrying ({self.request.retries + 1}/{self.max_retries + 1}): {error_reason}"
                    session.commit()
        except Exception as db_exc:
            logger.error(
                "Failed to update recording %d status in DB: %s",
                recording_id,
                db_exc,
                exc_info=True,
            )

        if quota_scope == "daily" or permanent:
            raise

        # For a short-window (per-minute) quota the API tells us exactly how
        # long to wait — honour that instead of guessing.
        countdown = _retry_delay_seconds(exc) or 15 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)

    finally:
        if tmp_audio and os.path.exists(tmp_audio):
            try:
                os.remove(tmp_audio)
            except OSError:
                logger.warning("Could not remove temp audio file %s", tmp_audio)


def _is_permanent(exc: Exception) -> bool:
    """
    True for failures that cannot possibly succeed on a retry.

    Retrying these burned two extra Gemini calls per recording and left the
    teacher watching "Retrying (2/3)" for minutes before the same verdict.
    """
    if isinstance(exc, FileNotFoundError):
        return True
    if isinstance(exc, EnvironmentError) and "GEMINI_API_KEY" in str(exc):
        return True
    if isinstance(exc, RuntimeError) and "could not transcribe" in str(exc).lower():
        return True
    return False


def _quota_scope(exc: Exception) -> str | None:
    """
    Return "daily", "short" or None for a Gemini 429.

    A 429 can mean either a per-minute burst limit (worth retrying in seconds)
    or an exhausted per-day allowance (not worth retrying at all today). The
    two are distinguished by the quotaId in the error payload.
    """
    msg = str(exc)
    if "RESOURCE_EXHAUSTED" not in msg and "429" not in msg:
        return None
    if "PerDay" in msg or "free_tier_requests" in msg:
        return "daily"
    return "short"


def _retry_delay_seconds(exc: Exception) -> float | None:
    """Extract the server-supplied retryDelay (e.g. '50s') from a 429 payload."""
    match = re.search(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'", str(exc))
    if match:
        # Small cushion so we don't come back a hair too early.
        return float(match.group(1)) + 5
    return None


def _classify_error(exc: Exception) -> str:
    """Convert raw exceptions into user-friendly error descriptions."""
    msg = str(exc)

    scope = _quota_scope(exc)
    if scope == "daily":
        return (
            "The daily AI usage quota for this project has been exhausted, so "
            "no further recordings can be analysed today. The quota resets at "
            "midnight Pacific time, or an administrator can raise it by "
            "enabling billing on the Gemini API key."
        )
    if scope == "short":
        return "The AI service is rate-limited right now. This will retry automatically in a moment."

    if isinstance(exc, FileNotFoundError):
        return "The audio file could not be found or downloaded. It may have been deleted or the link expired."

    if isinstance(exc, EnvironmentError) and "GEMINI_API_KEY" in msg:
        return "The AI service is not configured. Please contact your administrator."

    exc_module = getattr(type(exc), "__module__", "") or ""
    if "httpx" in exc_module or "HTTPStatusError" in type(exc).__name__:
        return f"Failed to download the audio file from storage: {msg}"

    if "ServerError" in type(exc).__name__ or "503" in msg:
        return "The AI service is temporarily unavailable. Please try again later."

    if isinstance(exc, ValueError) and "non-JSON" in msg:
        return "The AI returned an unreadable response. Please retry the analysis."

    if isinstance(exc, RuntimeError) and "timed out" in msg.lower():
        return "The AI service timed out while processing the audio. Please try again with a shorter recording or retry later."

    if isinstance(exc, RuntimeError) and "failed to process" in msg.lower():
        return "The AI service could not process the audio file. The file may be corrupted or in an unsupported format."

    if isinstance(exc, RuntimeError) and "could not transcribe" in msg.lower():
        # Already written for a teacher to read.
        return msg

    # Fallback: include the exception type and a truncated message
    exc_type = type(exc).__name__
    short_msg = msg[:200] if len(msg) > 200 else msg
    return f"An unexpected error occurred ({exc_type}): {short_msg}"

