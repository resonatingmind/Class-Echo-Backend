# ─────────────────────────────────────────────
#  config.py  –  weights, thresholds, constants
# ─────────────────────────────────────────────

import os

# gemini-2.5-flash is no longer served to newer API keys (404 NOT_FOUND), and
# the old gemini-1.5-flash fallback is long retired. Overridable by env so a
# future retirement doesn't need a code change.
# gemini-2.5-flash was retired for new API keys and now answers 404, so it is
# no longer usable as the default. Verified working against the live API.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

# Scoring is pinned to GEMINI_MODEL: a 503 is retried on the same model rather
# than handed to a fallback, so every report is scored by the same model and
# scores stay comparable. Transcription may still fall back (see evaluator).
SCORING_503_RETRY_DELAYS_S: tuple[float, ...] = (10, 30)

# Stamped on every report. Bump it whenever the prompt, the rubric, the
# response schema or the scoring model changes in a way that moves scores:
# trends must only compare reports scored under the same version.
#   1 — free-form JSON, string strengths/improvements (reports before this field)
#   2 — schema-constrained output at temperature 0, strengths with evidence,
#       improvements with a try-this script and one focus; segment citations
#       and speaker-aware question counts when the transcript is segmented;
#       a class_summary for the next-class brief (not scored)
RUBRIC_VERSION = "2"
SCORING_TEMPERATURE = 0.0

# ── Transcript quality gate (see transcript_metrics.check_transcript) ──
# Below this many words there is nothing to evaluate.
MIN_TRANSCRIPT_WORDS = 30
# Classroom speech runs ~80-160 wpm even with pauses; far below this means most
# of the audio was not transcribed.
MIN_TRANSCRIPT_WPM = 15
# The wpm check only applies once the recording is long enough to judge.
MIN_WPM_CHECK_DURATION_S = 120

# ── Scoring rubric weights (must sum to 100) ──
SCORE_WEIGHTS: dict[str, int] = {
    "verbal_clarity":          20,
    "pacing_delivery":         15,
    "content_structure":       15,
    "conceptual_depth":        20,
    "student_engagement":      15,
    "language_accessibility":  10,
    "closure_recap":            5,
}

assert sum(SCORE_WEIGHTS.values()) == 100, "Weights must sum to 100"

# ── LLM evaluation prompt (injected with context at runtime) ───────────────
EVALUATION_PROMPT_TEMPLATE = """
You are an expert educational evaluator analyzing a lecture transcript against the RAG reference context.

Evaluate the transcript carefully. The lecture may contain Hindi-English code-switching, regional Indian languages, or other multilingual mixing — evaluate the content as-is, understanding all languages present.

--- INPUTS ---
[Lecture Transcript]
{transcript}

--- RAG REFERENCE CONTEXT ---
Chapter Summary: {chapter_summary}
Learning Objectives: {learning_objectives}
Key Concepts / Must-Cover Concepts: {key_concepts}
Must-Cover Topics: {must_cover_topics}

--- LECTURE LEARNING PLAN (MICRO PLANNER) ---
{learning_plan_info}

--- MEASURED FACTS (computed from the audio and transcript; treat as ground truth) ---
{measured_facts}

Retrieved Reference Chunks:
{retrieved_chunks}
--------------

Please evaluate the lecture transcript against this reference context.

## Evaluation Dimensions & Scoring Rules

Score each dimension strictly and provide:
- A score within the stated maximum
- One precise finding sentence (what you observed, not generic)
- 2–3 short evidence quotes from the transcript (format: "finding text... 'quote'")

| Dimension | Max | Scoring Rules & Criteria |
|---|---|---|
| verbal_clarity | 20 | Pronunciation, sentence completion, filler words (um/uh/basically/you know), articulation, and verbal flow based on transcript. |
| pacing_delivery | 15 | Estimated speaking rate, pacing transitions, variation, structured pauses, and rhetorical delivery. |
| content_structure | 15 | Progression against chapter objectives, logical sequencing of topics, transitions, and introduction. |
| conceptual_depth | 20 | Correctness vs reference, curriculum coverage, explanation quality, and examples/analogies used to scaffold complex ideas. |
| student_engagement | 15 | Questions directed at students, checks for understanding, wait time, and conversational cues. |
| language_accessibility | 10 | Clarity of explanation, effectiveness of code-switching (if any), explaining technical jargon. |
| closure_recap | 5 | Summarizing key points at the end of the lecture and defining clear takeaways. |

## Curriculum Coverage & Accuracy Analysis
Additionally, perform a curriculum coverage and technical accuracy analysis:
1. curriculum_coverage:
   - Identify which key concepts and must-cover concepts from reference context were covered.
   - Identify which key concepts and must-cover concepts from reference context were missed.
   - Compute the coverage percentage (covered_concepts / total_concepts * 100).
2. accuracy_analysis:
   - Identify correct and clear explanations from the transcript.
   - Identify any potential inaccuracies or misconceptions compared to the RAG reference context.

## Quantitative Estimates
Speaking rate, filler count and question count are in MEASURED FACTS above. Use
those numbers when judging pacing, verbal clarity and engagement, and do not
contradict them in findings. Return 0 for wpm_estimate, filler_words_heard and
questions_asked; they are filled in from the measurements afterwards.
Estimate from the transcript:
- languages_detected: list of language names heard/used (e.g. ["Hindi", "English"])
- code_switching_frequency: "none" | "low" | "medium" | "high"

## Strengths
Return exactly 2 strengths, even for a weak lecture. Each must be a genuine,
specific thing this teacher did well in this lecture — never generic praise
("good effort", "nice voice"). For each give:
- area: the rubric dimension it belongs to
- text: one sentence naming what the teacher did and why it worked
- evidence: one short verbatim quote from the transcript that shows it

## Improvements
Return 1 to 3 improvements, most important first. Mark exactly one with
is_focus = true: the single change that would help this teacher most next class.
For each give:
- area: the rubric dimension it belongs to
- observation: what happened, in one plain sentence ("You moved on after asking a question without waiting.")
- try_this: a concrete script or action for the next class that takes under
  2 minutes — a sentence the teacher can say, or a step they can do. Match the
  subject and class level. Write it in the language the teacher teaches in: if
  the lecture is in Hinglish, write the script in Hinglish (Latin script).
- why: one line on why it helps students
Address the teacher as "you". No jargon.

## Class Summary
A short recap the teacher reads before the next class with the same students.
It describes the lesson, not the teacher, so never mention scores or feedback.
- covered: 3 or 4 short bullets, what was taught in this lecture
- unfinished: up to 3 bullets, learning plan items or key concepts that were not
  covered, or were rushed and need revisiting; empty if nothing was left over
- recall_questions: 2 or 3 quick questions the teacher can ask students at the
  start of the next class to check they remember this lesson, in the language
  the teacher teaches in
{segment_instructions}

## Output Format
Respond ONLY with a valid JSON object. Never omit any field. Every score must be an integer. If you are uncertain, estimate conservatively. If a field cannot be determined, return 0 or an empty array instead of removing it. Return exactly the schema shown below. Do not wrap the JSON in markdown formatting (like ```json), do not include any explanatory prose before or after the JSON.

{{
  "scores": {{
    "verbal_clarity":         {{ "score": <int 0-20>, "finding": "<one specific sentence>", "evidence": ["<quote 1>", "<quote 2>"] }},
    "pacing_delivery":        {{ "score": <int 0-15>, "finding": "<one specific sentence>", "evidence": ["<quote 1>", "<quote 2>"] }},
    "content_structure":      {{ "score": <int 0-15>, "finding": "<one specific sentence>", "evidence": ["<quote 1>", "<quote 2>"] }},
    "conceptual_depth":       {{ "score": <int 0-20>, "finding": "<one specific sentence>", "evidence": ["<quote 1>", "<quote 2>"] }},
    "student_engagement":     {{ "score": <int 0-15>, "finding": "<one specific sentence>", "evidence": ["<quote 1>", "<quote 2>"] }},
    "language_accessibility": {{ "score": <int 0-10>, "finding": "<one specific sentence>", "evidence": ["<quote 1>", "<quote 2>"] }},
    "closure_recap":          {{ "score": <int 0-5>,  "finding": "<one specific sentence>", "evidence": ["<quote 1>"] }}
  }},
  "total": <int 0-100>,
  "quantitative": {{
    "wpm_estimate": <int>,
    "filler_words_heard": <int>,
    "questions_asked": <int>,
    "languages_detected": ["<language>"],
    "code_switching_frequency": "<none|low|medium|high>"
  }},
  "overall_notes": "<2-3 sentence overall assessment of teaching effectiveness>",
  "strengths": [
    {{ "area": "<dimension>", "text": "<specific strength>", "evidence": "<short transcript quote>" }}
  ],
  "improvements": [
    {{ "area": "<dimension>", "observation": "<what happened>", "try_this": "<script or action for next class>", "why": "<one line>", "is_focus": <true for exactly one> }}
  ],
  "class_summary": {{
    "covered": ["<what was taught>"],
    "unfinished": ["<left over or rushed>"],
    "recall_questions": ["<question for students>"]
  }},
  "curriculum_coverage": {{
    "covered_concepts": [<list of covered concepts>],
    "missing_concepts": [<list of missed concepts>],
    "coverage_percentage": <float or int 0-100>
  }},
  "accuracy_analysis": {{
    "correct_explanations": [<list of correct explanation descriptions or quotes>],
    "potential_inaccuracies": [<list of potential inaccuracies or misconceptions found>]
  }},
  
  "confidence_analysis": {{
    "score": <int 0-100>,
    "hesitation_level": "<Low|Medium|High>",
    "confidence_notes": "<brief explanation>"
  }},

  "engagement_analysis": {{
    "score": <int 0-100>,
    "questions_asked": <int>,
    "examples_used": <int>,
    "student_checks": <int>,
    "interactive_prompts": <int>
  }},

  "speech_analysis": {{
    "clarity_score": <int 0-100>,
    "fluency_score": <int 0-100>,
    "pace_score": <int 0-100>,
    "filler_density": <int>
  }}
}}
""".strip().strip()


# ── Structured output schema for the scoring call ──────────────────────────
# Passed as response_json_schema so Gemini can only return this shape. The
# prompt above still describes each field; the schema enforces it.
_DIMENSION_ENUM = list(SCORE_WEIGHTS)

# The report's highlights strip, in display order.
HIGHLIGHT_KEYS = ("best_moment", "moment_to_rethink", "best_interaction")

# Filled into {segment_instructions} only when the transcript is segmented.
SEGMENT_INSTRUCTIONS = """
## Citing the moment
The transcript is split into numbered segments: [id] MM:SS SPEAKER: text.
TEACHER lines are the teacher; STUDENT lines are students. Judge the teacher on
TEACHER lines; use STUDENT lines to judge engagement (who answered, how often).
- For every evidence quote, also give the id of the segment it comes from, in
  evidence_segments (same order as evidence).
- For every strength and improvement, give segment_id: the segment that best
  shows it.
- highlights: pick best_moment (the strongest teaching moment),
  moment_to_rethink (the one moment most worth doing differently) and
  best_interaction (the best exchange with students; use a STUDENT segment's
  neighbour if needed). Each has segment_id and a one-line note to the teacher.
Only cite ids that appear in the transcript.
"""


def _dimension_schema(max_score: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "score": {"type": "integer", "minimum": 0, "maximum": max_score},
            "finding": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
            # Segment id of each evidence quote, same order. Only when the
            # transcript is segmented.
            "evidence_segments": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["score", "finding", "evidence"],
    }


def _int_object(*keys: str) -> dict:
    return {
        "type": "object",
        "properties": {k: {"type": "integer"} for k in keys},
        "required": list(keys),
    }


_STRING_LIST = {"type": "array", "items": {"type": "string"}}

SCORING_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            "properties": {dim: _dimension_schema(mx) for dim, mx in SCORE_WEIGHTS.items()},
            "required": _DIMENSION_ENUM,
        },
        "total": {"type": "integer", "minimum": 0, "maximum": 100},
        "quantitative": {
            "type": "object",
            "properties": {
                "wpm_estimate": {"type": "integer"},
                "filler_words_heard": {"type": "integer"},
                "questions_asked": {"type": "integer"},
                "languages_detected": _STRING_LIST,
                "code_switching_frequency": {
                    "type": "string", "enum": ["none", "low", "medium", "high"],
                },
            },
            "required": [
                "wpm_estimate", "filler_words_heard", "questions_asked",
                "languages_detected", "code_switching_frequency",
            ],
        },
        "overall_notes": {"type": "string"},
        "strengths": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "area": {"type": "string", "enum": _DIMENSION_ENUM},
                    "text": {"type": "string"},
                    "evidence": {"type": "string"},
                    "segment_id": {"type": "integer"},
                },
                "required": ["area", "text", "evidence"],
            },
        },
        "improvements": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "area": {"type": "string", "enum": _DIMENSION_ENUM},
                    "observation": {"type": "string"},
                    "try_this": {"type": "string"},
                    "why": {"type": "string"},
                    "is_focus": {"type": "boolean"},
                    "segment_id": {"type": "integer"},
                },
                "required": ["area", "observation", "try_this", "why", "is_focus"],
            },
        },
        "class_summary": {
            "type": "object",
            "properties": {
                "covered": {**_STRING_LIST, "maxItems": 4},
                "unfinished": {**_STRING_LIST, "maxItems": 3},
                "recall_questions": {**_STRING_LIST, "maxItems": 3},
            },
            "required": ["covered", "unfinished", "recall_questions"],
        },
        "curriculum_coverage": {
            "type": "object",
            "properties": {
                "covered_concepts": _STRING_LIST,
                "missing_concepts": _STRING_LIST,
                "coverage_percentage": {"type": "number", "minimum": 0, "maximum": 100},
            },
            "required": ["covered_concepts", "missing_concepts", "coverage_percentage"],
        },
        "accuracy_analysis": {
            "type": "object",
            "properties": {
                "correct_explanations": _STRING_LIST,
                "potential_inaccuracies": _STRING_LIST,
            },
            "required": ["correct_explanations", "potential_inaccuracies"],
        },
        "confidence_analysis": {
            "type": "object",
            "properties": {
                "score": {"type": "integer"},
                "hesitation_level": {"type": "string", "enum": ["Low", "Medium", "High"]},
                "confidence_notes": {"type": "string"},
            },
            "required": ["score", "hesitation_level", "confidence_notes"],
        },
        # Only when the transcript is segmented.
        "highlights": {
            "type": "object",
            "properties": {
                key: {
                    "type": "object",
                    "properties": {"segment_id": {"type": "integer"}, "note": {"type": "string"}},
                    "required": ["segment_id", "note"],
                }
                for key in HIGHLIGHT_KEYS
            },
        },
        "engagement_analysis": _int_object(
            "score", "questions_asked", "examples_used", "student_checks", "interactive_prompts",
        ),
        "speech_analysis": _int_object("clarity_score", "fluency_score", "pace_score", "filler_density"),
    },
    "required": [
        "scores", "total", "quantitative", "overall_notes", "strengths", "improvements",
        "class_summary", "curriculum_coverage", "accuracy_analysis", "confidence_analysis",
        "engagement_analysis", "speech_analysis",
    ],
}
