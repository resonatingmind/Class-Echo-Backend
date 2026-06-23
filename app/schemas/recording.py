from datetime import datetime
from pydantic import BaseModel, model_validator
from app.models.recording import RecordingStatus
from app.services.s3_service import sign_url


class RecordingOut(BaseModel):
    id: int
    subject_id: int
    chapter_id: int | None = None
    teacher_id: int
    chapter_name: str | None
    lecture_title: str | None = None
    description: str | None
    transcription: str | None = None
    cloudinary_url: str
    duration_seconds: float | None
    status: RecordingStatus
    error_reason: str | None = None
    is_archived: bool = False
    uploaded_at: datetime
    processed_at: datetime | None

    class Config:
        from_attributes = True

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


class LLMReportOut(BaseModel):
    id: int
    recording_id: int
    overall_score: float | None
    teaching_quality_notes: str | None
    score_breakdown: dict | None
    quantitative_metrics: dict | None
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
        # First run normal validation
        instance = handler(data)

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

        # Try to get chapter info from the related recording
        recording = None
        if hasattr(data, "recording"):
            recording = data.recording

        if recording:
            chapter = getattr(recording, "chapter", None)
            if chapter:
                doc_count = len(chapter.documents) if hasattr(chapter, "documents") else 0
                knowledge = getattr(chapter, "knowledge", None)
                learning_objs = []
                if knowledge:
                    learning_objs = knowledge.learning_objectives or []

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


class RecordingUpdate(BaseModel):
    chapter_id: int | None = None
    chapter_name: str | None = None
    lecture_title: str | None = None
    description: str | None = None

