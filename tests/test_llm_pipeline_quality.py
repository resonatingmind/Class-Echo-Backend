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

    def fake_transcribe(_path, _duration=None):
        seen["transcribing"] = step_now()
        return LESSON, None

    def fake_analyse(*_a, **_k):
        seen["analysing"] = step_now()
        raise analyse_error

    with patch("sqlalchemy.create_engine", return_value=engine), \
         patch("app.services.s3_service.sign_url", return_value=None), \
         patch("app.services.llm._download_audio", return_value="/nonexistent.mp3"), \
         patch("app.services.llm_pipeline.evaluator.transcribe_audio_segments", side_effect=fake_transcribe), \
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


# ── Structured scoring output (rubric 2) ─────────────────────────────────────

def test_scoring_call_is_schema_constrained_at_temperature_zero():
    client = MagicMock()
    client.models.generate_content.return_value = _Resp(_llm_json())
    with patch.object(evaluator, "_get_client", return_value=client):
        result = evaluator.evaluate(None, {"transcript": LESSON, "duration_seconds": 300})

    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.temperature == 0
    assert config.response_mime_type == "application/json"
    schema = config.response_json_schema
    assert set(schema["properties"]["scores"]["required"]) == set(evaluator.SCORE_WEIGHTS)
    assert schema["properties"]["strengths"]["minItems"] == 2
    assert schema["properties"]["improvements"]["maxItems"] == 3
    assert result["rubric_version"] == evaluator.RUBRIC_VERSION


def test_improvements_get_exactly_one_focus_listed_first():
    result = evaluator._validate_and_fix({
        "improvements": [
            {"area": "pacing_delivery", "observation": "a", "try_this": "t", "why": "w", "is_focus": False},
            {"area": "student_engagement", "observation": "b", "try_this": "t", "why": "w", "is_focus": True},
            {"area": "closure_recap", "observation": "c", "try_this": "t", "why": "w", "is_focus": True},
            {"area": "verbal_clarity", "observation": "d", "try_this": "t", "why": "w", "is_focus": False},
        ],
    })
    imps = result["improvements"]
    assert [i["observation"] for i in imps] == ["b", "a", "c"]
    assert [i["is_focus"] for i in imps] == [True, False, False]
    # Plain copies stay in step for older app builds.
    assert result["priority_improvements"] == ["b", "a", "c"]


def test_no_focus_marked_defaults_to_first_and_unknown_area_is_dropped():
    result = evaluator._validate_and_fix({
        "improvements": [{"area": "charisma", "observation": "a", "is_focus": False}, "not an object"],
        "strengths": [{"area": "verbal_clarity", "text": "s1", "evidence": "q"}, {"text": ""}],
    })
    assert result["improvements"] == [
        {"area": None, "observation": "a", "try_this": "", "why": "", "is_focus": True, "segment_id": None},
    ]
    assert result["strengths"] == [{"area": "verbal_clarity", "text": "s1", "evidence": "q", "segment_id": None}]
    assert result["top_strengths"] == ["s1"]


def test_report_out_lifts_legacy_string_findings():
    from app.schemas.recording import LLMReportOut

    out = LLMReportOut.model_validate({
        "id": 1, "recording_id": 1, "overall_score": 70, "teaching_quality_notes": "",
        "score_breakdown": {}, "created_at": datetime(2026, 10, 1),
        "quantitative_metrics": {"top_strengths": ["s1", "s2", "s3"], "priority_improvements": ["i1", "i2"]},
        "raw_llm_response": {"model": "old"},
    })
    assert [s.text for s in out.strengths] == ["s1", "s2"]
    assert [(i.observation, i.is_focus, i.try_this) for i in out.improvements] == [
        ("i1", True, ""), ("i2", False, ""),
    ]


def test_report_out_prefers_structured_findings():
    from app.schemas.recording import LLMReportOut

    out = LLMReportOut.model_validate({
        "id": 1, "recording_id": 1, "overall_score": 70, "teaching_quality_notes": "",
        "score_breakdown": {}, "created_at": datetime(2026, 10, 1), "rubric_version": "2",
        "quantitative_metrics": {"top_strengths": ["plain"], "priority_improvements": ["plain"]},
        "raw_llm_response": {
            "strengths": [{"area": "verbal_clarity", "text": "s", "evidence": "q"}],
            "improvements": [{"area": "closure_recap", "observation": "o", "try_this": "t", "why": "w", "is_focus": True}],
        },
    })
    assert out.strengths[0].evidence == "q"
    assert out.improvements[0].try_this == "t"
    assert out.rubric_version == "2"


# ── Timestamped segments (phase 5) ───────────────────────────────────────────

from app.services.llm_pipeline import segments as seg  # noqa: E402

SEGMENTS = [
    {"id": 0, "start": 0.0, "end": 8.0, "speaker": "teacher", "text": "Today we start linear equations. What is a variable?"},
    {"id": 1, "start": 8.0, "end": 10.0, "speaker": "student", "text": "A letter? Like x?"},
    {"id": 2, "start": 10.0, "end": 20.0, "speaker": "teacher", "text": "Yes, x stands for an unknown number, um, any number."},
]


def test_parse_timestamp_accepts_clock_and_numbers():
    assert seg.parse_timestamp("04:31") == 271
    assert seg.parse_timestamp("1:02:07") == 3727
    assert seg.parse_timestamp("12.5") == 12.5
    assert seg.parse_timestamp(90) == 90.0
    assert seg.parse_timestamp("soon") is None
    assert seg.parse_timestamp(-3) is None


def test_normalize_segments_cleans_order_speakers_and_bounds():
    out = seg.normalize_segments({"segments": [
        {"start": "00:05", "end": "00:09", "speaker": "teacher", "text": " Hello class "},
        {"start": "00:03", "end": "00:02", "speaker": "chorus", "text": "Good morning"},
        {"start": "00:20", "end": "00:25", "speaker": "student", "text": ""},
        {"start": "never", "end": "00:30", "speaker": "student", "text": "lost"},
        {"start": "09:00", "end": "09:05", "speaker": "student", "text": "past the end"},
    ]}, duration_seconds=60)
    assert out == [
        {"id": 0, "start": 5.0, "end": 9.0, "speaker": "teacher", "text": "Hello class"},
        # Earlier time pinned forward, end fixed, unknown speaker -> teacher.
        {"id": 1, "start": 5.0, "end": 5.0, "speaker": "teacher", "text": "Good morning"},
    ]


def test_metrics_with_speakers_count_only_teacher_questions():
    text = seg.join_segments(SEGMENTS)
    plain = compute_metrics(text, 600)
    split = compute_metrics(text, 600, SEGMENTS)
    assert plain["questions"] == 3
    assert split["questions"] == 1
    assert split["teacher_talk_share"] == 0.9
    assert split["word_count"] == plain["word_count"]


def _audio_ref():
    return MagicMock(uri="u", mime_type="audio/mpeg")


def test_segmented_transcription_returns_text_and_segments():
    client = MagicMock()
    client.models.generate_content.return_value = _Resp(json.dumps({"segments": [
        {"start": "00:00", "end": "00:08", "speaker": "teacher", "text": "Hello"},
        {"start": "00:08", "end": "00:10", "speaker": "student", "text": "Hi"},
    ]}))
    with patch.object(evaluator, "_get_client", return_value=client), \
            patch.object(evaluator, "_upload_and_wait", return_value=_audio_ref()):
        text, segments = evaluator.transcribe_audio_segments("/x.mp3", 600)
    assert text == "Hello Hi"
    assert [s["speaker"] for s in segments] == ["teacher", "student"]
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.response_json_schema == seg.SEGMENTS_RESPONSE_SCHEMA


def test_unreadable_segments_fall_back_to_plain_transcript():
    truncated = json.dumps({"segments": [{"start": "00:00", "text": "cut"}]})[:-12]
    client = MagicMock()
    client.models.generate_content.side_effect = [_Resp(truncated), _Resp("Plain words")]
    with patch.object(evaluator, "_get_client", return_value=client), \
            patch.object(evaluator, "_upload_and_wait", return_value=_audio_ref()):
        text, segments = evaluator.transcribe_audio_segments("/x.mp3")
    assert (text, segments) == ("Plain words", None)


def test_evaluation_with_segments_cites_moments():
    llm = json.loads(_llm_json())
    llm["scores"]["student_engagement"].update(evidence=["What is a variable?", "Like x?"], evidence_segments=[0, 99])
    llm["strengths"] = [{"area": "verbal_clarity", "text": "s", "evidence": "q", "segment_id": 2}]
    llm["improvements"] = [
        {"area": "closure_recap", "observation": "o", "try_this": "t", "why": "w", "is_focus": True, "segment_id": 7},
    ]
    llm["highlights"] = {
        "best_moment": {"segment_id": 2, "note": "clear"},
        "moment_to_rethink": {"segment_id": 42, "note": "bad id"},
    }
    client = MagicMock()
    client.models.generate_content.return_value = _Resp(json.dumps(llm))
    context = {"transcript": seg.join_segments(SEGMENTS), "transcript_segments": SEGMENTS, "duration_seconds": 600}
    with patch.object(evaluator, "_get_client", return_value=client):
        result = evaluator.evaluate(None, context)

    prompt = client.models.generate_content.call_args.kwargs["contents"]
    assert "[1] 00:08 STUDENT: A letter? Like x?" in prompt
    assert "## Citing the moment" in prompt
    assert "Questions asked by the teacher: 1" in prompt
    assert result["scores"]["student_engagement"]["evidence_starts"] == [0.0, None]
    assert "evidence_segments" not in result["scores"]["student_engagement"]
    assert (result["strengths"][0]["segment_id"], result["strengths"][0]["start"]) == (2, 10.0)
    assert (result["improvements"][0]["segment_id"], result["improvements"][0]["start"]) == (None, None)
    assert result["highlights"] == {"best_moment": {"segment_id": 2, "start": 10.0, "note": "clear"}}
    assert result["quantitative"]["teacher_talk_share"] == 0.9


def test_evaluation_without_segments_has_no_citation_section():
    client = MagicMock()
    client.models.generate_content.return_value = _Resp(_llm_json())
    with patch.object(evaluator, "_get_client", return_value=client):
        result = evaluator.evaluate(None, {"transcript": LESSON, "duration_seconds": 300})
    assert "## Citing the moment" not in client.models.generate_content.call_args.kwargs["contents"]
    assert result["highlights"] == {}
