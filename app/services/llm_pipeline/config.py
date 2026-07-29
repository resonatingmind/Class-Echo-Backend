# ─────────────────────────────────────────────
#  config.py  –  weights, thresholds, constants
# ─────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.5-flash"

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
Estimate from the transcript:
- wpm_estimate: approximate words per minute (integer)
- filler_words_heard: total count of filler words heard in the transcript (um, uh, basically, you know, sort of, etc.)
- questions_asked: number of questions directed at students
- languages_detected: list of language names heard/used (e.g. ["Hindi", "English"])
- code_switching_frequency: "none" | "low" | "medium" | "high"

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
  "top_strengths": ["<specific strength 1>", "<specific strength 2>"],
  "priority_improvements": ["<improvement 1>", "<improvement 2>", "<improvement 3>"],
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
