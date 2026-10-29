"""
Timestamped transcript segments.

Transcription returns the lecture as segments with a start/end time and a
speaker, so every finding can point at the exact moment it describes and
student speech stops being counted as the teacher's. The plain transcript is
still stored, joined from the segments, so everything that reads it keeps
working.

Stored shape (recordings.transcript_segments):
    [{"id": 0, "start": 12.0, "end": 19.5, "speaker": "teacher", "text": "..."}]
"""
from __future__ import annotations

import re

SPEAKERS = ("teacher", "student")

TRANSCRIBE_SEGMENTS_PROMPT = (
    "You are an expert transcriber. Transcribe this classroom recording exactly "
    "as spoken. Do not summarize or omit anything. Capture all content "
    "accurately, including any code-switching between Hindi and English "
    "(Hinglish) or other Indian regional languages.\n\n"
    "Split the transcript into segments of one or two sentences, in order. For "
    "each segment give:\n"
    "- start and end: when it is spoken, as MM:SS (or H:MM:SS past an hour), "
    "measured from the start of the audio\n"
    "- speaker: teacher for the person leading the class, student for anyone "
    "else (answers, questions, reading aloud, chorus replies)\n"
    "- text: the exact words"
)

SEGMENTS_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "speaker": {"type": "string", "enum": list(SPEAKERS)},
                    "text": {"type": "string"},
                },
                "required": ["start", "end", "speaker", "text"],
            },
        },
    },
    "required": ["segments"],
}

_CLOCK_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,3}):(\d{1,2}(?:\.\d+)?)\s*$")


def parse_timestamp(value) -> float | None:
    """'04:31', '1:02:07', '271', 271.5 -> seconds. None when unreadable."""
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    if not isinstance(value, str):
        return None
    m = _CLOCK_RE.match(value)
    if m:
        hours, minutes, seconds = m.groups()
        return int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds)
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number >= 0 else None


def format_clock(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def normalize_segments(raw, duration_seconds: float | None = None) -> list[dict]:
    """
    Clean what the model returned: drop empty or untimed segments, keep times
    within the audio and in order, and number the segments from 0.
    """
    items = raw.get("segments") if isinstance(raw, dict) else raw
    out: list[dict] = []
    last_start = 0.0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        start = parse_timestamp(item.get("start"))
        if not text or start is None:
            continue
        if duration_seconds and start > duration_seconds + 5:
            continue
        # Times must not run backwards; a stray earlier time is pinned to the last one.
        start = max(start, last_start)
        end = parse_timestamp(item.get("end"))
        if end is None or end < start:
            end = start
        if duration_seconds:
            end = min(end, duration_seconds)
        speaker = item.get("speaker") if item.get("speaker") in SPEAKERS else "teacher"
        out.append({"id": len(out), "start": round(start, 1), "end": round(end, 1), "speaker": speaker, "text": text})
        last_start = start
    return out


def join_segments(segments: list[dict]) -> str:
    return " ".join(s["text"] for s in segments)


def teacher_text(segments: list[dict]) -> str:
    return " ".join(s["text"] for s in segments if s["speaker"] == "teacher")


def teacher_talk_share(segments: list[dict]) -> float | None:
    """Share of spoken time that is the teacher's, 0-1. None without usable timings."""
    teacher = student = 0.0
    for s in segments:
        length = max(0.0, s["end"] - s["start"])
        if s["speaker"] == "teacher":
            teacher += length
        else:
            student += length
    total = teacher + student
    return round(teacher / total, 3) if total > 0 else None


def format_for_prompt(segments: list[dict]) -> str:
    """One numbered line per segment, so the evaluator can cite segment ids."""
    return "\n".join(
        f"[{s['id']}] {format_clock(s['start'])} {s['speaker'].upper()}: {s['text']}" for s in segments
    )
