from datetime import datetime
import enum

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm.attributes import set_committed_value
from app.models.recording import RecordingStatus
from app.models.report_feedback import FeedbackTarget, FeedbackVerdict
from app.models.self_rating import SELF_RATING_AREAS
from app.services.s3_service import sign_url
from app.schemas.learning_plan import LearningPlanOut


def _loaded_relationship(obj, name: str):
    """Return obj.<name> only if already eager-loaded, without triggering a
    lazy load — lazy loads aren't safe from a sync pydantic validator running
    outside the async greenlet."""
    try:
        state = sa_inspect(obj)
    except Exception:
        return None
    if name in state.unloaded:
        return None
    return getattr(obj, name)


class RecordingOut(BaseModel):
    id: int
    subject_id: int
    chapter_id: int | None = None
    learning_plan_id: int | None = None
    learning_plan: LearningPlanOut | None = None
    teacher_id: int
    chapter_name: str | None
    lecture_title: str | None = None
    description: str | None
    transcription: str | None = None
    transcript_segments: list[dict] | None = None
    cloudinary_url: str
    duration_seconds: float | None
    status: RecordingStatus
    error_reason: str | None = None
    processing_step: str | None = None
    is_archived: bool = False
    uploaded_at: datetime
    processed_at: datetime | None

    class Config:
        from_attributes = True

    @model_validator(mode="wrap")
    @classmethod
    def _no_lazy_relationship_loads(cls, data, handler):
        """Pydantic reads every declared field off the ORM object. Reading a
        relationship that wasn't eager-loaded triggers a lazy load, which raises
        MissingGreenlet under the async engine and turns the whole response into
        a 500 (that is what happened to recordings bound to a learning plan).
        Pin unloaded relationships to None first — without marking the instance
        dirty — so serialization can never emit SQL."""
        if not isinstance(data, dict):
            try:
                state = sa_inspect(data)
            except Exception:
                state = None
            if state is not None:
                for name in ("learning_plan", "report"):
                    if name in state.unloaded:
                        set_committed_value(data, name, None)
        return handler(data)

    @model_validator(mode="after")
    def sign_s3_url(self) -> "RecordingOut":
        if self.cloudinary_url:
            self.cloudinary_url = sign_url(self.cloudinary_url)
        return self


class CurriculumCoverageOut(BaseModel):
    covered_concepts: list[str] = []
    missing_concepts: list[str] = []
    coverage_percentage: float = 0.0


class AccuracyAnalysisOut(BaseModel):
    correct_explanations: list[str] = []
    potential_inaccuracies: list[str] = []


class ChapterInfoOut(BaseModel):
    chapter_name: str | None = None
    document_count: int = 0
    learning_objectives: list[str] = []


class LearningObjectiveStatus(BaseModel):
    objective: str
    status: str = "missing"   # "achieved" | "partial" | "missing"


class StrengthOut(BaseModel):
    area: str | None = None
    text: str
    evidence: str = ""
    # The cited transcript moment; None without a segmented transcript.
    segment_id: int | None = None
    start: float | None = None


class ImprovementOut(BaseModel):
    area: str | None = None
    observation: str
    try_this: str = ""
    why: str = ""
    is_focus: bool = False
    segment_id: int | None = None
    start: float | None = None


class HighlightOut(BaseModel):
    segment_id: int
    start: float
    note: str = ""


class SelfRatingIn(BaseModel):
    # {area: 1-5}; areas from models.self_rating.SELF_RATING_AREAS. Empty when skipped.
    ratings: dict[str, int] = {}
    reflection: str | None = Field(default=None, max_length=500)
    skipped: bool = False

    @model_validator(mode="after")
    def _check(self) -> "SelfRatingIn":
        unknown = set(self.ratings) - set(SELF_RATING_AREAS)
        if unknown:
            raise ValueError(f"Unknown areas: {', '.join(sorted(unknown))}")
        if any(not 1 <= v <= 5 for v in self.ratings.values()):
            raise ValueError("Ratings must be 1 to 5")
        if not self.skipped and not self.ratings:
            raise ValueError("Rate at least one area, or skip")
        if self.skipped:
            self.ratings, self.reflection = {}, None
        return self


class SelfRatingOut(BaseModel):
    ratings: dict[str, int] = {}
    reflection: str | None = None
    skipped: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class FeedbackReason(str, enum.Enum):
    score_too_low = "score_too_low"
    score_too_high = "score_too_high"
    quote_out_of_context = "quote_out_of_context"
    transcript_wrong = "transcript_wrong"
    class_type = "class_type"            # lab / test / revision
    student_context = "student_context"  # students' situation not considered
    other = "other"


class ReportFeedbackOut(BaseModel):
    target_type: FeedbackTarget
    target_key: str
    area: str | None = None
    verdict: FeedbackVerdict
    reasons: list[str] = []
    comment: str | None = None
    updated_at: datetime

    class Config:
        from_attributes = True


class ReportFeedbackIn(BaseModel):
    target_type: FeedbackTarget
    target_key: str = Field(min_length=1, max_length=50)
    verdict: FeedbackVerdict
    reasons: list[FeedbackReason] = []
    comment: str | None = Field(default=None, max_length=1000)


class SharedClipIn(BaseModel):
    start: float = Field(ge=0)
    title: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class SharedClipOut(BaseModel):
    id: int
    start: float
    title: str
    note: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


# Report relationships that only some endpoints eager-load.
_OPTIONAL_REPORT_RELATIONS = ("feedback", "self_rating", "shared_clips")


class _ReportView:
    """Read-through wrapper over an LLMReport that shows its unloaded optional
    relationships as None instead of triggering a lazy load. Unlike
    set_committed_value it leaves the shared ORM instance untouched."""

    def __init__(self, report, hidden: set[str]):
        self._report = report
        self._hidden = hidden

    def __getattr__(self, name):
        if name in self._hidden:
            return None
        return getattr(self._report, name)


def _fill_findings(instance: "LLMReportOut", data) -> None:
    raw = data.get("raw_llm_response") if isinstance(data, dict) else getattr(data, "raw_llm_response", None)
    raw = raw if isinstance(raw, dict) else {}
    if isinstance(raw.get("strengths"), list):
        instance.strengths = [StrengthOut(**s) for s in raw["strengths"] if isinstance(s, dict) and s.get("text")]
    if isinstance(raw.get("improvements"), list):
        instance.improvements = [
            ImprovementOut(**i) for i in raw["improvements"] if isinstance(i, dict) and i.get("observation")
        ]
    if isinstance(raw.get("highlights"), dict):
        instance.highlights = {
            k: HighlightOut(**h) for k, h in raw["highlights"].items()
            if isinstance(h, dict) and h.get("segment_id") is not None and h.get("start") is not None
        }

    qm = instance.quantitative_metrics or {}
    if not instance.strengths:
        instance.strengths = [StrengthOut(text=str(s)) for s in qm.get("top_strengths") or [] if s][:2]
    if not instance.improvements:
        instance.improvements = [
            ImprovementOut(observation=str(s), is_focus=i == 0)
            for i, s in enumerate(x for x in qm.get("priority_improvements") or [] if x)
        ][:3]


class LLMReportOut(BaseModel):
    id: int
    recording_id: int
    overall_score: float | None
    teaching_quality_notes: str | None
    score_breakdown: dict | None
    quantitative_metrics: dict | None
    rubric_version: str | None = None
    # The teacher left this lecture out of My Growth trends.
    excluded_from_trends: bool = False
    # Structured findings. Reports scored before rubric 2 only have plain
    # strings, which are lifted into this shape with the extra fields empty.
    strengths: list[StrengthOut] = []
    improvements: list[ImprovementOut] = []
    # Only filled where the endpoint eager-loads it (the detail views).
    feedback: list[ReportFeedbackOut] | None = None
    # best_moment / moment_to_rethink / best_interaction; empty without segments.
    highlights: dict[str, HighlightOut] = {}
    # The teacher's own view, given before the reveal. Teacher endpoint only.
    self_rating: SelfRatingOut | None = None
    # Moments the teacher chose to show their school admin. Detail views only.
    shared_clips: list[SharedClipOut] | None = None
    curriculum_coverage: CurriculumCoverageOut | None = None
    accuracy_analysis: AccuracyAnalysisOut | None = None
    chapter_info: ChapterInfoOut | None = None
    learning_objectives_achievement: list[LearningObjectiveStatus] | None = None
    created_at: datetime

    class Config:
        from_attributes = True

    @model_validator(mode="wrap")
    @classmethod
    def extract_rag_fields(cls, data, handler):
        """Extract RAG evaluation data from raw_llm_response when serializing from ORM."""
        # feedback is a relationship: pin it to None unless already loaded, or
        # reading it triggers an async lazy load (see RecordingOut above).
        source = data
        if not isinstance(data, dict):
            try:
                state = sa_inspect(data)
            except Exception:
                state = None
            hidden = {n for n in _OPTIONAL_REPORT_RELATIONS if state is not None and n in state.unloaded}
            if hidden:
                source = _ReportView(data, hidden)

        # First run normal validation
        instance = handler(source)
        _fill_findings(instance, data)

        # Try to get raw_llm_response from the ORM model
        raw = None
        if hasattr(data, "raw_llm_response"):
            raw = data.raw_llm_response
        elif isinstance(data, dict):
            raw = data.get("raw_llm_response")

        if raw and isinstance(raw, dict):
            # Extract curriculum_coverage
            cc = raw.get("curriculum_coverage")
            if cc and isinstance(cc, dict):
                instance.curriculum_coverage = CurriculumCoverageOut(
                    covered_concepts=cc.get("covered_concepts", []),
                    missing_concepts=cc.get("missing_concepts", []),
                    coverage_percentage=float(cc.get("coverage_percentage", 0)),
                )

            # Extract accuracy_analysis
            aa = raw.get("accuracy_analysis")
            if aa and isinstance(aa, dict):
                instance.accuracy_analysis = AccuracyAnalysisOut(
                    correct_explanations=aa.get("correct_explanations", []),
                    potential_inaccuracies=aa.get("potential_inaccuracies", []),
                )

        # Try to get chapter info from the related recording — only ever via
        # already eager-loaded relationships, since this validator runs
        # synchronously and can't safely trigger an async lazy load.
        recording = None if isinstance(data, dict) else _loaded_relationship(data, "recording")

        if recording:
            chapter = _loaded_relationship(recording, "chapter")
            if chapter:
                documents = _loaded_relationship(chapter, "documents")
                doc_count = len(documents) if documents is not None else 0
                knowledge = _loaded_relationship(chapter, "knowledge")
                learning_objs = knowledge.learning_objectives or [] if knowledge else []

                instance.chapter_info = ChapterInfoOut(
                    chapter_name=chapter.name,
                    document_count=doc_count,
                    learning_objectives=learning_objs,
                )

                # Build learning objectives achievement by cross-referencing
                # covered concepts from curriculum_coverage
                if learning_objs and instance.curriculum_coverage:
                    covered = set(
                        c.lower() for c in (instance.curriculum_coverage.covered_concepts or [])
                    )
                    missing = set(
                        c.lower() for c in (instance.curriculum_coverage.missing_concepts or [])
                    )
                    achievement = []
                    for obj in learning_objs:
                        obj_lower = obj.lower()
                        if any(kw in obj_lower or obj_lower in kw for kw in covered):
                            status = "achieved"
                        elif any(kw in obj_lower or obj_lower in kw for kw in missing):
                            status = "missing"
                        else:
                            status = "partial"
                        achievement.append(
                            LearningObjectiveStatus(objective=obj, status=status)
                        )
                    instance.learning_objectives_achievement = achievement

        return instance


class RecordingWithReport(RecordingOut):
    report: LLMReportOut | None = None
    # The school admin's view: no report, transcript or audio beyond the
    # moments in shared_clips (services/admin_privacy.py).
    report_private: bool = False
    shared_clips: list[SharedClipOut] | None = None


class RecordingUpdate(BaseModel):
    chapter_id: int | None = None
    chapter_name: str | None = None
    lecture_title: str | None = None
    description: str | None = None

