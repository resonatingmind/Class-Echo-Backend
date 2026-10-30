"""What a school admin may see of a teacher's lectures.

Decided 29 Oct (roadmap open decision 1): the admin sees each teacher's trends
and status, plus only what the teacher chose to share. Reports, per-lecture
scores, transcripts and the full audio stay with the teacher. The super admin
is not a school admin and keeps full access through its own endpoints.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_report import LLMReport
from app.models.recording import Recording
from app.models.shared_clip import SharedClip
from app.schemas.growth import SharedClipForAdminOut
from app.schemas.recording import RecordingWithReport, SharedClipOut
from app.schemas.teacher_recordings import TeacherRecordingOut
from app.services.s3_service import sign_url

PRIVATE_DETAIL = "Reports are private to the teacher. You can see their growth and anything they share."

# Same window the app plays around a moment (ClipPlayback.tsx).
CLIP_LEAD_SECONDS = 10
CLIP_TAIL_SECONDS = 20


def clip_segments(segments: list[dict] | None, starts: list[float]) -> list[dict]:
    """Only the transcript lines inside a shared clip's window."""
    return [
        s for s in segments or []
        if any(s.get("end", 0) >= t - CLIP_LEAD_SECONDS and s.get("start", 0) <= t + CLIP_TAIL_SECONDS for t in starts)
    ]


def private_recording(recording: Recording) -> RecordingWithReport:
    """A lecture as its school admin sees it: what and when, and the moments the teacher shared."""
    clips = list(recording.report.shared_clips) if recording.report else []
    out = RecordingWithReport.model_validate(recording)
    out.report = None
    out.transcription = None
    out.report_private = True
    out.shared_clips = [SharedClipOut.model_validate(c) for c in clips]
    out.transcript_segments = clip_segments(recording.transcript_segments, [c.start for c in clips])
    if not clips:
        out.cloudinary_url = ""
    return out


def private_list(rows: list[TeacherRecordingOut]) -> list[TeacherRecordingOut]:
    """Lecture lists without scores or audio."""
    for row in rows:
        row.overall_score = None
        row.cloudinary_url = ""
    return rows


async def shared_clips_for_admin(db: AsyncSession, teacher_id: int) -> list[SharedClipForAdminOut]:
    result = await db.execute(
        select(SharedClip, Recording)
        .join(LLMReport, LLMReport.id == SharedClip.report_id)
        .join(Recording, Recording.id == LLMReport.recording_id)
        .where(SharedClip.teacher_id == teacher_id, Recording.is_archived.is_(False))
        .order_by(SharedClip.created_at.desc())
    )
    return [
        SharedClipForAdminOut(
            id=clip.id,
            start=clip.start,
            title=clip.title,
            note=clip.note,
            recording_id=rec.id,
            lecture_title=rec.lecture_title or rec.chapter_name,
            recorded_at=rec.uploaded_at,
            audio_url=sign_url(rec.cloudinary_url) or rec.cloudinary_url,
            segments=clip_segments(rec.transcript_segments, [clip.start]),
        )
        for clip, rec in result.all()
    ]
