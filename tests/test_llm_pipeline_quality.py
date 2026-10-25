"""
Unit tests for the analysis-quality fixes: transcript gate, computed metrics,
pinned scoring model and stuck-recording recovery.

None of these touch the shared test database, so they can also be run on their
own with `pytest --noconftest tests/test_llm_pipeline_quality.py`.
"""
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from google.genai import errors as genai_errors
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — registers all mappers
from app.models.recording import Recording, RecordingStatus
from app.services.llm_pipeline import evaluator
from app.services.llm_pipeline.transcript_metrics import (
    TranscriptTooShortError,
    apply_metrics,
    check_transcript,
    compute_metrics,
)
from app.tasks import llm_tasks

LESSON = " ".join(["word"] * 200)


# ── Transcript gate ──────────────────────────────────────────────────────────

def test_gate_rejects_single_word_transcript():
    # Recording 21: a 9-minute Hindi lesson that transcribed as one word.
    with pytest.raises(TranscriptTooShortError) as exc:
        check_transcript("مستعمل", 522)
    assert "could not transcribe" in str(exc.value)
    assert llm_tasks._is_permanent(exc.value)


def test_gate_rejects_transcript_far_too_short_for_duration():
    with pytest.raises(TranscriptTooShortError, match="10-minute"):
        check_transcript(" ".join(["word"] * 100), 600)  # 10 wpm


def test_gate_accepts_real_lesson_and_unknown_duration():
    check_transcript(LESSON, 600)  # 20 wpm
    check_transcript(LESSON, None)
    check_transcript(LESSON, 0.0)


def test_gate_skips_wpm_check_on_short_clips():
    check_transcript(" ".join(["word"] * 40), 90)


# ── Computed metrics ─────────────────────────────────────────────────────────

def test_compute_metrics_counts_words_fillers_and_questions():
    text = (
        "Um, so today, you know, we study light. Basically light bends. "
        "Kya samjhe? Matlab refraction. Any doubts? उम्मीद है मतलब समझ आया?"
    )
    m = compute_metrics(text, 60)
    assert m["word_count"] == len(text.split())
    assert m["wpm"] == m["word_count"]
    # um, you know, basically, matlab, मतलब — and not the "um" inside umbrella-like words
    assert m["filler_words"] == 5
    assert m["questions"] == 3


def test_filler_match_needs_word_boundaries():
    assert compute_metrics("umbrella uhuru summary ahead", 60)["filler_words"] == 0


def test_compute_metrics_without_duration_reports_zero_wpm():
    assert compute_metrics(LESSON, None)["wpm"] == 0


def test_apply_metrics_overwrites_llm_guesses():
    result = {
        "quantitative": {"wpm_estimate": 140, "filler_words_heard": 9, "questions_asked": 4},
        "engagement_analysis": {"questions_asked": 4},
        "speech_analysis": {"filler_density": 7},
    }
    metrics = {"word_count": 1000, "wpm": 71, "filler_words": 20, "questions": 12, "filler_density": 2}
    apply_metrics(result, metrics)
    assert result["quantitative"]["wpm_estimate"] == 71
    assert result["quantitative"]["filler_words_heard"] == 20
    assert result["quantitative"]["questions_asked"] == 12
    assert result["engagement_analysis"]["questions_asked"] == 12
    assert result["speech_analysis"]["filler_density"] == 2


# ── Pinned scoring model ─────────────────────────────────────────────────────

class _Resp:
    def __init__(self, text):
        self.text = text


def _server_error():
    return genai_errors.ServerError(503, {"error": {"message": "overloaded"}})


def _llm_json():
    dims = {k: {"score": v, "finding": "f", "evidence": []} for k, v in evaluator.SCORE_WEIGHTS.items()}
    return json.dumps({
        "scores": dims,
        "quantitative": {"wpm_estimate": 140, "filler_words_heard": 3, "questions_asked": 2},
    })


def test_scoring_retries_same_model_on_503_and_never_falls_back():
    client = MagicMock()
    client.models.generate_content.side_effect = [_server_error(), _server_error(), _Resp(_llm_json())]
    with patch.object(evaluator, "_get_client", return_value=client), \
            patch.object(evaluator.time, "sleep"):
        result = evaluator.evaluate(None, {"transcript": LESSON, "duration_seconds": 300})

    models = [c.kwargs["model"] for c in client.models.generate_content.call_args_list]
    assert models == [evaluator.GEMINI_MODEL] * 3
    assert result["scoring_model"] == evaluator.GEMINI_MODEL
    # Measured, not the LLM's 140 / 3 / 2.
    assert result["quantitative"]["wpm_estimate"] == 40
    assert result["quantitative"]["filler_words_heard"] == 0
    assert result["quantitative"]["questions_asked"] == 0
    prompt = client.models.generate_content.call_args.kwargs["contents"]
    assert "Speaking rate: 40 words per minute" in prompt


def test_scoring_gives_up_after_retries_so_celery_can_back_off():
    client = MagicMock()
    client.models.generate_content.side_effect = _server_error()
    with patch.object(evaluator, "_get_client", return_value=client), \
            patch.object(evaluator.time, "sleep"):
        with pytest.raises(genai_errors.ServerError):
            evaluator.evaluate(None, {"transcript": LESSON})
    assert client.models.generate_content.call_count == len(evaluator.SCORING_503_RETRY_DELAYS_S) + 1


def test_retired_scoring_model_fails_permanently():
    client = MagicMock()
    client.models.generate_content.side_effect = genai_errors.ClientError(
        404, {"error": {"message": "404 NOT_FOUND"}}
    )
    with patch.object(evaluator, "_get_client", return_value=client):
        with pytest.raises(evaluator.ScoringModelUnavailableError) as exc:
            evaluator.evaluate(None, {"transcript": LESSON})
    assert client.models.generate_content.call_count == 1
    assert llm_tasks._is_permanent(exc.value)


# ── Stuck-recording recovery ─────────────────────────────────────────────────

@pytest.fixture
def sqlite_session():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _no_fks(conn, _):
        conn.execute("PRAGMA foreign_keys=OFF")

    Recording.__table__.create(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _rec(session, status, age):
    rec = Recording(
        subject_id=1, teacher_id=1, cloudinary_url=f"https://x/{status}-{age}.mp3",
        cloudinary_public_id="p", status=status,
        uploaded_at=datetime(2026, 10, 21, 12) - age,
    )
    session.add(rec)
    session.commit()
    return rec


def test_recovery_fails_old_processing_and_requeues_old_pending(sqlite_session):
    s = sqlite_session
    old_proc = _rec(s, RecordingStatus.processing, timedelta(days=30))
    old_proc.processing_step = "analysing"
    s.commit()
    new_proc = _rec(s, RecordingStatus.processing, timedelta(minutes=20))
    old_pend = _rec(s, RecordingStatus.pending, timedelta(hours=3))
    new_pend = _rec(s, RecordingStatus.pending, timedelta(minutes=5))
    done = _rec(s, RecordingStatus.completed, timedelta(days=30))

    with patch.object(llm_tasks.process_recording, "delay") as delay:
        summary = llm_tasks.recover_stuck_recordings(s, now=datetime(2026, 10, 21, 12))

    assert summary == {"failed": [old_proc.id], "requeued": [old_pend.id]}
    delay.assert_called_once_with(old_pend.id, old_pend.cloudinary_url)
    s.expire_all()
    assert old_proc.status == RecordingStatus.failed
    assert "Retry analysis" in old_proc.error_reason
    assert old_proc.processing_step is None
    assert new_proc.status == RecordingStatus.processing
    assert new_pend.status == RecordingStatus.pending
    assert done.status == RecordingStatus.completed


# ── Processing step ──────────────────────────────────────────────────────────

def _run_task_on(session, rec, analyse_error):
    """Run process_recording against the sqlite session, recording the step the
    app would see while transcription and scoring are under way."""
    engine = session.get_bind()
    seen = {}

    def step_now():
        with sessionmaker(bind=engine)() as s:
            return s.get(Recording, rec.id).processing_step

    def fake_transcribe(_path):
        seen["transcribing"] = step_now()
        return LESSON

    def fake_analyse(*_a, **_k):
        seen["analysing"] = step_now()
        raise analyse_error

    with patch("sqlalchemy.create_engine", return_value=engine), \
         patch("app.services.s3_service.sign_url", return_value=None), \
         patch("app.services.llm._download_audio", return_value="/nonexistent.mp3"), \
         patch("app.services.llm_pipeline.evaluator.transcribe_audio", side_effect=fake_transcribe), \
         patch.object(llm_tasks, "analyze_recording", side_effect=fake_analyse), \
         pytest.raises(type(analyse_error)):
        llm_tasks.process_recording.run(rec.id, rec.cloudinary_url)
    session.expire_all()
    return seen


def test_processing_step_follows_transcription_then_analysis(sqlite_session):
    rec = _rec(sqlite_session, RecordingStatus.pending, timedelta(0))

    # A permanent error ends the task at once, so the final state is checkable.
    seen = _run_task_on(sqlite_session, rec, FileNotFoundError("gone"))

    assert seen == {"transcribing": "transcribing", "analysing": "analysing"}
    assert rec.status == RecordingStatus.failed
    assert rec.processing_step is None


def test_processing_step_skips_transcribing_when_transcript_uploaded(sqlite_session):
    rec = _rec(sqlite_session, RecordingStatus.pending, timedelta(0))
    rec.transcription = LESSON
    sqlite_session.commit()

    seen = _run_task_on(sqlite_session, rec, FileNotFoundError("gone"))

    assert "transcribing" not in seen
    assert seen["analysing"] == "analysing"
