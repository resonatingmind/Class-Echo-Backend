from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.recording import Recording
from app.models.chapter import Chapter
from app.models.llm_report import LLMReport


async def get_recording_detail(
    db: AsyncSession,
    recording_id: int,
):
    result = await db.execute(
        select(Recording)
        .options(
            selectinload(Recording.teacher),
            selectinload(Recording.subject),
            selectinload(Recording.report).selectinload(LLMReport.recording),
            selectinload(Recording.learning_plan),
            selectinload(Recording.chapter).selectinload(
                Chapter.documents
            ),
            selectinload(Recording.chapter).selectinload(
                Chapter.knowledge
            ),
        )
        .where(Recording.id == recording_id)
    )

    return result.scalar_one_or_none()