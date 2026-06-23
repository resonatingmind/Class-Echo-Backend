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
FALLBACK_MODEL = "gemini-1.5-flash"

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
    # Poll until ACTIVE or FAILED (usually a few seconds)
    for _ in range(30):
        f = client.files.get(name=uploaded.name)
        state = f.state.name if hasattr(f.state, "name") else str(f.state)
        if state in ("ACTIVE", "FAILED"):
            if state == "FAILED":
                raise RuntimeError("Gemini Files API failed to process the audio file.")
            return f
        time.sleep(2)
    raise RuntimeError("Timed out waiting for Gemini Files API to process audio.")


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

    prompt = EVALUATION_PROMPT_TEMPLATE.format(
        transcript=transcript,
        chapter_summary=summary,
        learning_objectives=objectives,
        key_concepts=concepts,
        must_cover_topics=must_cover,
        retrieved_chunks=chunks_str
    )

    for model in [GEMINI_MODEL, FALLBACK_MODEL]:
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )
            return response.text
        except genai_errors.ServerError:
            if model == FALLBACK_MODEL:
                raise
            logger.warning("Model %s unavailable (503), falling back to %s", model, FALLBACK_MODEL)


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
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_uri(
                    file_uri=file_ref.uri,
                    mime_type=file_ref.mime_type,
                ),
                prompt,
            ],
        )
        return response.text or ""
    finally:
        if file_ref is not None:
            try:
                client.files.delete(name=file_ref.name)
            except Exception:
                pass


def evaluate(audio_path: str, reference_context: dict | None = None) -> dict:
    """
    Evaluate the lecture transcript and reference context using Gemini.
    """
    client = _get_client()
    if not reference_context:
        reference_context = {}

    if not reference_context.get("transcript"):
        logger.info("Transcript not found in reference_context. Transcribing audio first...")
        reference_context["transcript"] = transcribe_audio(audio_path)

    raw = _call_gemini(client, reference_context)
    parsed = _parse_response(raw)
    return _validate_and_fix(parsed)
