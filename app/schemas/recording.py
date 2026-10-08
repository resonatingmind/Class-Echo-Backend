from datetime import datetime
from pydantic import BaseModel, model_validator
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm.attributes import set_committed_value
from app.models.recording import RecordingStatus
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
    cloudinary_url: str
    duration_seconds: float | None
    status: RecordingStatus
    error_reason: str | None = None
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


class LectureCoverageOut(BaseModel):
    topic: str | None = None
    sub_topic: str | None = None
    planned_concepts: list[str] = []
    covered_concepts: list[str] = []
    missed_concepts: list[str] = []
    coverage_percentage: float = 0.0
    alignment_status: str | None = "fully_aligned"
    notes: str | None = None


class ChapterCoverageOut(BaseModel):
    chapter_name: str | None = None
    total_concepts_count: int = 0
    covered_in_lecture: list[str] = []
    remaining_chapter_concepts: list[str] = []
    chapter_coverage_percentage: float = 0.0


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
    lecture_coverage: LectureCoverageOut | None = None
    chapter_coverage: ChapterCoverageOut | None = None
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

            # Extract explicit lecture_coverage if present
            lc = raw.get("lecture_coverage")
            if lc and isinstance(lc, dict):
                instance.lecture_coverage = LectureCoverageOut(
                    topic=lc.get("topic"),
                    sub_topic=lc.get("sub_topic"),
                    planned_concepts=lc.get("planned_concepts", []),
                    covered_concepts=lc.get("covered_concepts", []),
                    missed_concepts=lc.get("missed_concepts", []),
                    coverage_percentage=float(lc.get("coverage_percentage", 0)),
                    alignment_status=lc.get("alignment_status", "fully_aligned"),
                    notes=lc.get("notes"),
                )

            # Extract explicit chapter_coverage if present
            chc = raw.get("chapter_coverage")
            if chc and isinstance(chc, dict):
                instance.chapter_coverage = ChapterCoverageOut(
                    chapter_name=chc.get("chapter_name"),
                    total_concepts_count=int(chc.get("total_concepts_count", 0)),
                    covered_in_lecture=chc.get("covered_in_lecture", []),
                    remaining_chapter_concepts=chc.get("remaining_chapter_concepts", []),
                    chapter_coverage_percentage=float(chc.get("chapter_coverage_percentage", 0)),
                )

            # Extract accuracy_analysis
            aa = raw.get("accuracy_analysis")
            if aa and isinstance(aa, dict):
                instance.accuracy_analysis = AccuracyAnalysisOut(
                    correct_explanations=aa.get("correct_explanations", []),
                    potential_inaccuracies=aa.get("potential_inaccuracies", []),
                )

        # Try to get chapter info and learning plan from the related recording
        recording = None if isinstance(data, dict) else _loaded_relationship(data, "recording")

        if recording:
            chapter = _loaded_relationship(recording, "chapter")
            learning_plan = _loaded_relationship(recording, "learning_plan")

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
                # covered concepts from curriculum_coverage or lecture_coverage
                covered_pool = []
                if instance.lecture_coverage and instance.lecture_coverage.covered_concepts:
                    covered_pool = instance.lecture_coverage.covered_concepts
                elif instance.curriculum_coverage and instance.curriculum_coverage.covered_concepts:
                    covered_pool = instance.curriculum_coverage.covered_concepts

                if learning_objs and covered_pool:
                    covered = set(c.lower() for c in covered_pool)
                    missing = set(
                        c.lower() for c in (
                            (instance.lecture_coverage and instance.lecture_coverage.missed_concepts)
                            or (instance.curriculum_coverage and instance.curriculum_coverage.missing_concepts)
                            or []
                        )
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

            # Backward-compatible synthesis: if lecture_coverage or chapter_coverage
            # were not explicitly produced by the LLM in older records, synthesize them
            # so the dual-angle view works seamlessly for existing reports!
            if instance.curriculum_coverage:
                cov = instance.curriculum_coverage.covered_concepts or []
                miss = instance.curriculum_coverage.missing_concepts or []
                cov_pct = instance.curriculum_coverage.coverage_percentage

                # Synthesize lecture_coverage if missing
                if not instance.lecture_coverage:
                    lp_topic = learning_plan.topic_name if learning_plan else None
                    lp_sub = learning_plan.sub_topic if learning_plan else (recording.lecture_title or None)
                    
                    # If coverage_percentage was 100% (or high), the missed list from LLM was
                    # likely the other chapter concepts, not failed concepts of this lecture.
                    if cov_pct >= 90.0:
                        plan_cov = cov
                        plan_miss = []
                        eff_pct = 100.0
                    else:
                        plan_cov = cov
                        plan_miss = miss[:2] if len(miss) > 5 else miss
                        total_plan = len(plan_cov) + len(plan_miss)
                        eff_pct = (len(plan_cov) / total_plan * 100.0) if total_plan > 0 else cov_pct

                    instance.lecture_coverage = LectureCoverageOut(
                        topic=lp_topic,
                        sub_topic=lp_sub,
                        planned_concepts=plan_cov + plan_miss,
                        covered_concepts=plan_cov,
                        missed_concepts=plan_miss,
                        coverage_percentage=eff_pct,
                        alignment_status="fully_aligned" if not plan_miss else "partially_aligned",
                        notes=f"Delivered planned concepts for {lp_sub or 'the lecture'}." if lp_sub else None,
                    )

                # Synthesize chapter_coverage if missing
                if not instance.chapter_coverage:
                    ch_name = (chapter.name if chapter else None) or recording.chapter_name or "Chapter"
                    rem_concepts = miss if len(miss) > 0 else []
                    tot_count = len(cov) + len(rem_concepts)
                    ch_pct = round((len(cov) / tot_count * 100.0), 1) if tot_count > 0 else 0.0

                    instance.chapter_coverage = ChapterCoverageOut(
                        chapter_name=ch_name,
                        total_concepts_count=tot_count,
                        covered_in_lecture=cov,
                        remaining_chapter_concepts=rem_concepts,
                        chapter_coverage_percentage=ch_pct,
                    )

        return instance


class RecordingWithReport(RecordingOut):
    report: LLMReportOut | None = None

    @model_validator(mode="after")
    def ensure_dual_angle_coverage(self) -> "RecordingWithReport":
        if not self.report or not self.report.curriculum_coverage:
            return self

        cov = self.report.curriculum_coverage.covered_concepts or []
        miss = self.report.curriculum_coverage.missing_concepts or []
        cov_pct = self.report.curriculum_coverage.coverage_percentage

        # Ensure lecture_coverage is set
        if not self.report.lecture_coverage:
            lp_topic = self.learning_plan.topic_name if self.learning_plan else None
            lp_sub = self.learning_plan.sub_topic if self.learning_plan else (self.lecture_title or None)

            if cov_pct >= 90.0:
                plan_cov = cov
                plan_miss = []
                eff_pct = 100.0
            else:
                plan_cov = cov
                plan_miss = miss[:2] if len(miss) > 5 else miss
                tot = len(plan_cov) + len(plan_miss)
                eff_pct = (len(plan_cov) / tot * 100.0) if tot > 0 else cov_pct

            self.report.lecture_coverage = LectureCoverageOut(
                topic=lp_topic,
                sub_topic=lp_sub,
                planned_concepts=plan_cov + plan_miss,
                covered_concepts=plan_cov,
                missed_concepts=plan_miss,
                coverage_percentage=eff_pct,
                alignment_status="fully_aligned" if not plan_miss else "partially_aligned",
                notes=f"Covered planned concepts for {lp_sub or 'this lecture'}." if (lp_sub or lp_topic) else None,
            )

        # Ensure chapter_coverage is set
        if not self.report.chapter_coverage:
            ch_name = (self.report.chapter_info.chapter_name if self.report.chapter_info else None) or self.chapter_name or "Chapter"
            rem_concepts = miss if len(miss) > 0 else []
            tot_count = len(cov) + len(rem_concepts)
            ch_pct = round((len(cov) / tot_count * 100.0), 1) if tot_count > 0 else 0.0

            self.report.chapter_coverage = ChapterCoverageOut(
                chapter_name=ch_name,
                total_concepts_count=tot_count,
                covered_in_lecture=cov,
                remaining_chapter_concepts=rem_concepts,
                chapter_coverage_percentage=ch_pct,
            )

        return self


class RecordingUpdate(BaseModel):
    chapter_id: int | None = None
    chapter_name: str | None = None
    lecture_title: str | None = None
    description: str | None = None

