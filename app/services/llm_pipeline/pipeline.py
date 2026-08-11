from __future__ import annotations

import logging
import os

from .evaluator import evaluate

logger = logging.getLogger(__name__)


def run_pipeline(audio_path: str | None, reference_context: dict | None = None) -> dict:
    """
    Evaluate a lecture audio file using Gemini's native audio processing.

    Steps: upload to Gemini Files API → single evaluation call → return result.

    ``audio_path`` may be None when ``reference_context`` already carries a
    transcript — the evaluation call is text-only, so there is nothing to
    download or upload in that case.

    Returns
    -------
    {
        "total":   int (0-100),
        "scores":  { dim: { score, finding, evidence } },
        "quantitative": { wpm_estimate, filler_words_heard, questions_asked,
                          languages_detected, code_switching_frequency },
        "overall_notes":         str,
        "top_strengths":         [str],
        "priority_improvements": [str],
    }
    """
    needs_audio = not (reference_context or {}).get("transcript")
    if needs_audio:
        if not audio_path or not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        logger.info("Uploading audio to Gemini Files API: %s", audio_path)

    result = evaluate(audio_path, reference_context=reference_context)
    logger.info("Evaluation complete — total score: %d/100", result["total"])
    return result
