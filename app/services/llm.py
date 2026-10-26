from __future__ import annotations

import os
import tempfile

import httpx

from .llm_pipeline import run_pipeline


def _download_audio(url: str) -> str:
    suffix = os.path.splitext(url.split("?")[0])[-1] or ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        r = client.get(url)
        r.raise_for_status()
        with open(tmp_path, "wb") as f:
            f.write(r.content)
    return tmp_path


def analyze_recording(
    cloudinary_url: str,
    reference_context: dict | None = None,
    audio_path: str | None = None,
) -> dict:
    """
    Evaluate a lecture recording with Gemini audio and return results shaped for
    LLMReport storage.

    Pass ``audio_path`` when the caller has already downloaded the file — the
    download is the second-largest cost in the pipeline and doing it twice for
    the same recording is pure waste. When given, the caller owns the file and
    is responsible for deleting it; otherwise the file is fetched from
    ``cloudinary_url`` and cleaned up here.

    Returns
    -------
    {
        "overall_score":          int (0-100),
        "teaching_quality_notes": str,
        "score_breakdown":        dict,  per-dimension {score, finding, evidence}
        "quantitative_metrics":   dict,  wpm, fillers, questions, languages, strengths, improvements
        "raw_llm_response":       dict,
        "rubric_version":         str,
    }
    """
    # The evaluation call is text-only. If a transcript is already available
    # there is no reason to fetch the audio at all.
    needs_audio = not (reference_context or {}).get("transcript")

    owns_file = audio_path is None and needs_audio
    tmp_path = audio_path or (_download_audio(cloudinary_url) if needs_audio else None)
    try:
        result = run_pipeline(tmp_path, reference_context=reference_context)
    finally:
        if owns_file and tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {
        "overall_score":          result["total"],
        "teaching_quality_notes": result["overall_notes"],
        "score_breakdown":        result["scores"],
        "quantitative_metrics": {
            **result["quantitative"],
            "top_strengths":         result["top_strengths"],
            "priority_improvements": result["priority_improvements"],
        },
        "raw_llm_response": result,
        "rubric_version":   result["rubric_version"],
    }
