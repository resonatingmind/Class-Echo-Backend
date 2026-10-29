"""
Countable lecture metrics, computed from the transcript instead of asked of the
LLM.

The evaluator used to have Gemini "estimate" words per minute, filler words and
questions from a plain-text transcript that carries no timing at all. Re-scoring
the same transcript gave wpm values clustered at 125-142 regardless of the
lecture, about 26% off what transcript length / audio duration implies, and
filler and question counts that moved between runs. Counting is a job for code.
"""
from __future__ import annotations

import re

from .segments import teacher_talk_share, teacher_text
from .config import (
    MIN_TRANSCRIPT_WORDS,
    MIN_TRANSCRIPT_WPM,
    MIN_WPM_CHECK_DURATION_S,
)

# Multi-word fillers are matched as phrases; single words on word boundaries.
# Kept conservative: words like "like", "actually" or "okay" are too often
# meaningful to count blindly.
_FILLER_PATTERNS = [
    r"u+m+", r"u+h+", r"e+r+m*", r"a+h+", r"h+m+",
    r"you know", r"basically", r"sort of", r"i mean",
    r"matlab", "मतलब",
]
_FILLER_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(_FILLER_PATTERNS) + r")(?!\w)",
    re.IGNORECASE,
)
# ASCII and Arabic-script question marks; Devanagari text uses the ASCII one.
_QUESTION_RE = re.compile(r"[?؟]")


class TranscriptTooShortError(RuntimeError):
    """
    The transcript cannot carry a meaningful evaluation.

    The message contains "could not transcribe" on purpose: llm_tasks treats
    that phrase as permanent (no retry) and shows the message to the teacher
    as-is.
    """


def count_words(transcript: str) -> int:
    return len(transcript.split())


def check_transcript(transcript: str | None, duration_seconds: float | None) -> None:
    """
    Raise TranscriptTooShortError when the transcript is too thin to score.

    Scoring such a transcript produced a 0/100 "report" for what was, in one
    case, a real 9-minute Hindi lesson that transcribed as a single word. A
    teacher reading that 0 has no way to tell a broken transcription from a
    bad lesson.
    """
    words = count_words(transcript or "")
    if words < MIN_TRANSCRIPT_WORDS:
        raise TranscriptTooShortError(
            f"The AI service could not transcribe enough speech from this "
            f"recording to evaluate it (only {words} word"
            f"{'' if words == 1 else 's'} recognised). Check that the "
            f"recording contains the lesson audio and tap Retry analysis."
        )
    if duration_seconds and duration_seconds >= MIN_WPM_CHECK_DURATION_S:
        wpm = words / (duration_seconds / 60)
        if wpm < MIN_TRANSCRIPT_WPM:
            minutes = round(duration_seconds / 60)
            raise TranscriptTooShortError(
                f"The AI service could not transcribe most of this "
                f"{minutes}-minute recording (only {words} words recognised). "
                f"The audio may be too quiet or noisy. Tap Retry analysis to "
                f"transcribe it again."
            )


def compute_metrics(
    transcript: str, duration_seconds: float | None, segments: list[dict] | None = None,
) -> dict:
    """
    Return the countable metrics for a transcript.

    wpm is 0 when the duration is unknown; the detail screen renders 0 as "—".
    With speaker-labelled segments, fillers and questions count the teacher's
    speech only and the teacher's share of talk time is measured; without
    them every question mark counts, students' included.
    """
    teacher = teacher_text(segments) if segments else transcript
    words = count_words(transcript)
    fillers = len(_FILLER_RE.findall(teacher))
    questions = len(_QUESTION_RE.findall(teacher))
    wpm = round(words / (duration_seconds / 60)) if duration_seconds and duration_seconds > 0 else 0
    return {
        "word_count": words,
        "wpm": wpm,
        "filler_words": fillers,
        "questions": questions,
        # Fillers per 100 words, so long and short lectures compare fairly.
        "filler_density": round(fillers / words * 100) if words else 0,
        "speakers_known": bool(segments),
        # Teacher's share of spoken time, 0-1; None without segments.
        "teacher_talk_share": teacher_talk_share(segments) if segments else None,
    }


def apply_metrics(result: dict, metrics: dict) -> dict:
    """Overwrite the LLM's guesses in an evaluation result with measured values."""
    quant = result.setdefault("quantitative", {})
    quant["wpm_estimate"] = metrics["wpm"]
    quant["filler_words_heard"] = metrics["filler_words"]
    quant["questions_asked"] = metrics["questions"]
    quant["word_count"] = metrics["word_count"]
    quant["teacher_talk_share"] = metrics.get("teacher_talk_share")

    result.setdefault("engagement_analysis", {})["questions_asked"] = metrics["questions"]
    result.setdefault("speech_analysis", {})["filler_density"] = metrics["filler_density"]
    return result


def format_for_prompt(metrics: dict, duration_seconds: float | None) -> str:
    """Measured facts handed to the evaluator, so its prose agrees with the numbers."""
    duration = f"{duration_seconds / 60:.1f} minutes" if duration_seconds else "unknown"
    wpm = f"{metrics['wpm']} words per minute" if metrics["wpm"] else "unknown (no duration)"
    lines = [
        f"Recording duration: {duration}",
        f"Transcript length: {metrics['word_count']} words",
        f"Speaking rate: {wpm}",
        f"Filler words counted: {metrics['filler_words']}",
    ]
    if metrics.get("speakers_known"):
        share = metrics.get("teacher_talk_share")
        lines.append(f"Questions asked by the teacher: {metrics['questions']}")
        lines.append(
            f"Teacher's share of talk time: {round(share * 100)}%" if share is not None
            else "Teacher's share of talk time: unknown"
        )
    else:
        lines.append(f"Question marks in transcript (teacher and students): {metrics['questions']}")
    return "\n".join(lines)
