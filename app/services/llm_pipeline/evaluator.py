from __future__ import annotations

import json
import logging
import os
import re
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .config import EVALUATION_PROMPT_TEMPLATE, GEMINI_MODEL, SCORE_WEIGHTS

logger = logging.getLogger(__name__)
FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash")

# Per-request timeouts (milliseconds). Without these a slow Gemini response
# hangs the worker indefinitely instead of failing fast into a retry.
TRANSCRIBE_TIMEOUT_MS = 15 * 60 * 1000
EVALUATE_TIMEOUT_MS = 10 * 60 * 1000

# Files API processing deadline for a single upload (seconds).
FILE_ACTIVE_TIMEOUT_S = 300

_client: genai.Client | None = None

_MIME_MAP = {
    ".mp3":  "audio/mpeg",
    ".mp4":  "audio/mp4",
    ".m4a":  "audio/mp4",
    ".wav":  "audio/wav",
    ".ogg":  "audio/ogg",
    ".webm": "audio/webm",
    ".aac":  "audio/aac",
    ".flac": "audio/flac",
}


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        from app.config import settings
        api_key = os.environ.get("GEMINI_API_KEY") or (settings.GEMINI_API_KEY if hasattr(settings, "GEMINI_API_KEY") else None)
        if not api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY is not set. Export it before running the pipeline."
            )
        _client = genai.Client(api_key=api_key)
    return _client


def _mime_type(audio_path: str) -> str:
    ext = os.path.splitext(audio_path)[-1].lower()
    return _MIME_MAP.get(ext, "audio/mpeg")


def _upload_and_wait(client: genai.Client, audio_path: str):
    """Upload audio to Gemini Files API and wait until it is ACTIVE."""
    mime = _mime_type(audio_path)
    uploaded = client.files.upload(
        file=audio_path,
        config={"mime_type": mime, "display_name": "lecture_audio"},
    )
    # Poll until ACTIVE or FAILED. Short files are usually ready almost
    # immediately, so start with a small sleep and back off — a flat 2s poll
    # wasted up to two seconds on every recording. Long lectures need far more
    # headroom than the old 60s ceiling allowed.
    deadline = time.monotonic() + FILE_ACTIVE_TIMEOUT_S
    delay = 0.5
    while True:
        f = client.files.get(name=uploaded.name)
        state = f.state.name if hasattr(f.state, "name") else str(f.state)
        if state == "FAILED":
            raise RuntimeError("Gemini Files API failed to process the audio file.")
        if state == "ACTIVE":
            return f
        if time.monotonic() + delay > deadline:
            raise RuntimeError("Timed out waiting for Gemini Files API to process audio.")
        time.sleep(delay)
        delay = min(delay * 1.5, 5.0)


def _call_gemini(client: genai.Client, reference_context: dict | None = None) -> str:
    if not reference_context:
        reference_context = {}

    transcript = reference_context.get("transcript") or "N/A"
    summary = reference_context.get("chapter_summary") or "N/A"
    
    objectives_list = reference_context.get("learning_objectives") or []
    objectives = ", ".join(objectives_list) if isinstance(objectives_list, list) else str(objectives_list)
    if not objectives:
        objectives = "N/A"

    concepts_list = reference_context.get("key_concepts") or []
    concepts = ", ".join(concepts_list) if isinstance(concepts_list, list) else str(concepts_list)
    if not concepts:
        concepts = "N/A"

    must_cover_list = reference_context.get("must_cover_topics") or []
    must_cover = ", ".join(must_cover_list) if isinstance(must_cover_list, list) else str(must_cover_list)
    if not must_cover:
        must_cover = "N/A"

    chunks_str = ""
    for idx, c in enumerate(reference_context.get("retrieved_chunks") or []):
        chunks_str += f"\nChunk {idx+1}:\n{c.get('chunk_text', '')}\n"
    if not chunks_str:
        chunks_str = "No reference material chunks retrieved."

    lp = reference_context.get("learning_plan")
    if lp and isinstance(lp, dict):
        learning_plan_info = (
            f"Date: {lp.get('date') or 'N/A'}\n"
            f"Chapter: {lp.get('topic_name') or 'N/A'}\n"
            f"Lecture: {lp.get('sub_topic') or 'N/A'}\n"
            f"Classwork, Key Strategies & Activities: {lp.get('classwork_activities') or 'N/A'}\n"
            f"Assessment / Extension Task: {lp.get('assessment_task') or 'N/A'}\n"
            f"Teaching Aids: {lp.get('teaching_aids') or 'N/A'}\n"
            f"Learning Outcomes: {lp.get('learning_outcomes') or 'N/A'}"
        )
    elif reference_context.get("chapter_learning_plans"):
        plans_list = reference_context.get("chapter_learning_plans")
        learning_plan_info = "Chapter Lecture Plans:\n" + "\n".join(
            [
                f"- Chapter: {p.get('topic_name')} | Lecture: {p.get('sub_topic')} | Activities: {p.get('classwork_activities')} | Outcomes: {p.get('learning_outcomes')}"
                for p in plans_list
            ]
        )
    else:
        learning_plan_info = "No specific Lecture Planner entry selected for this lecture."

    prompt = EVALUATION_PROMPT_TEMPLATE.format(
        transcript=transcript,
        chapter_summary=summary,
        learning_objectives=objectives,
        key_concepts=concepts,
        must_cover_topics=must_cover,
        learning_plan_info=learning_plan_info,
        retrieved_chunks=chunks_str
    )

    for model in [GEMINI_MODEL, FALLBACK_MODEL]:
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    http_options=types.HttpOptions(timeout=EVALUATE_TIMEOUT_MS),
                ),
            )
            return response.text
        except genai_errors.ServerError:
            if model == FALLBACK_MODEL:
                raise
            logger.warning("Model %s unavailable (503), falling back to %s", model, FALLBACK_MODEL)
        except genai_errors.ClientError as exc:
            # A retired or ungated model answers 404, not 503 — without this the
            # fallback never engaged and every recording failed outright.
            if model == FALLBACK_MODEL or "404" not in str(exc):
                raise
            logger.warning("Model %s not available (404), falling back to %s", model, FALLBACK_MODEL)


def _parse_response(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Gemini returned non-JSON output.\nRaw:\n{raw}\nError: {exc}"
        ) from exc


def _validate_and_fix(result: dict) -> dict:
    scores = result.get("scores", {})
    for dim, max_val in SCORE_WEIGHTS.items():
        if dim not in scores:
            scores[dim] = {"score": 0, "finding": "", "evidence": []}
        entry = scores[dim]
        if isinstance(entry, (int, float)):
            scores[dim] = {"score": max(0, min(int(entry), max_val)), "finding": "", "evidence": []}
        else:
            entry["score"] = max(0, min(int(entry.get("score", 0)), max_val))
            entry["finding"] = str(entry.get("finding", ""))
            entry["evidence"] = [str(e) for e in entry.get("evidence", [])]

    result["scores"] = scores
    result["total"] = sum(v["score"] for v in scores.values())

    quant = result.get("quantitative", {})
    quant.setdefault("wpm_estimate", 0)
    quant.setdefault("filler_words_heard", 0)
    quant.setdefault("questions_asked", 0)
    quant.setdefault("languages_detected", [])
    quant.setdefault("code_switching_frequency", "none")
    result["quantitative"] = quant

    result.setdefault("overall_notes", "")
    result["top_strengths"] = [str(s) for s in result.get("top_strengths", [])]
    result["priority_improvements"] = [str(s) for s in result.get("priority_improvements", [])]

    # Handle curriculum_coverage and accuracy_analysis
    cc = result.setdefault("curriculum_coverage", {})
    cc.setdefault("covered_concepts", [])
    cc.setdefault("missing_concepts", [])
    cc.setdefault("coverage_percentage", 0.0)

    aa = result.setdefault("accuracy_analysis", {})
    aa.setdefault("correct_explanations", [])
    aa.setdefault("potential_inaccuracies", [])

    # Confidence Analysis
    confidence = result.setdefault("confidence_analysis", {})
    confidence.setdefault("score", 0)
    confidence.setdefault("hesitation_level", "Unknown")
    confidence.setdefault("confidence_notes", "")

    # Engagement Analysis
    engagement = result.setdefault("engagement_analysis", {})
    engagement.setdefault("score", 0)
    engagement.setdefault("questions_asked", 0)
    engagement.setdefault("examples_used", 0)
    engagement.setdefault("student_checks", 0)
    engagement.setdefault("interactive_prompts", 0)

    # Speech Analysis
    speech = result.setdefault("speech_analysis", {})
    speech.setdefault("clarity_score", 0)
    speech.setdefault("fluency_score", 0)
    speech.setdefault("pace_score", 0)
    speech.setdefault("filler_density", 0)
    return result


def transcribe_audio(audio_path: str) -> str:
    """Upload audio to Gemini Files API, request transcription, and clean up."""
    client = _get_client()
    file_ref = None
    try:
        file_ref = _upload_and_wait(client, audio_path)
        prompt = (
            "You are an expert transcriber. Transcribe the following audio recording exactly as spoken. "
            "Do not summarize or omit anything. Capture all content accurately, including any code-switching "
            "between Hindi and English (Hinglish) or other Indian regional languages."
        )
        started = time.monotonic()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_uri(
                    file_uri=file_ref.uri,
                    mime_type=file_ref.mime_type,
                ),
                prompt,
            ],
            config=types.GenerateContentConfig(
                # Verbatim transcription gains nothing from reasoning tokens,
                # and flash models think by default — this is the single
                # largest chunk of processing latency. Gemini 3.x rejects
                # thinking_budget outright, so use thinking_level.
                thinking_config=types.ThinkingConfig(thinking_level="low"),
                http_options=types.HttpOptions(timeout=TRANSCRIBE_TIMEOUT_MS),
            ),
        )
        logger.info("Transcription completed in %.1fs", time.monotonic() - started)
        return response.text or ""
    finally:
        if file_ref is not None:
            try:
                client.files.delete(name=file_ref.name)
            except Exception:
                pass


def evaluate(audio_path: str | None, reference_context: dict | None = None) -> dict:
    """
    Evaluate the lecture transcript and reference context using Gemini.
    """
    client = _get_client()
    if not reference_context:
        reference_context = {}

    if not reference_context.get("transcript"):
        if not audio_path:
            raise FileNotFoundError(
                "No transcript in reference_context and no audio file to transcribe."
            )
        logger.info("Transcript not found in reference_context. Transcribing audio first...")
        reference_context["transcript"] = transcribe_audio(audio_path)

    raw = _call_gemini(client, reference_context)
    parsed = _parse_response(raw)
    return _validate_and_fix(parsed)
