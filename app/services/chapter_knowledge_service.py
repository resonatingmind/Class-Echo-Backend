import os
import logging
from datetime import datetime
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from google import genai
from google.genai import types

from app.config import settings
from app.models.chapter_knowledge import ChapterKnowledge
from app.models.chapter_chunk import ChapterChunk
from app.models.chapter_document import ChapterDocument

logger = logging.getLogger(__name__)


class ChapterKnowledgeJSON(BaseModel):
    chapter_summary: str = Field(description="A comprehensive summary of the chapter's core content")
    learning_objectives: list[str] = Field(description="List of key learning objectives students should achieve")
    key_concepts: list[str] = Field(description="List of main concepts introduced in this chapter")
    must_cover_topics: list[str] = Field(description="List of critical topics that must be covered during classes")
    common_misconceptions: list[str] = Field(description="List of common misconceptions or student errors")


def generate_chapter_knowledge(session: Session, chapter_id: int) -> ChapterKnowledge | None:
    """
    Synchronous function for Celery task.
    Collects all chunk texts from completed documents for the chapter,
    sends them to Gemini 2.5 Flash using structured output,
    and updates/inserts a ChapterKnowledge record.
    """
    # 1. Fetch all completed document chunks for the chapter
    chunks = (
        session.query(ChapterChunk)
        .join(ChapterDocument, ChapterChunk.document_id == ChapterDocument.id)
        .filter(
            ChapterChunk.chapter_id == chapter_id,
            ChapterDocument.processing_status == "COMPLETED"
        )
        .order_by(ChapterChunk.id)
        .all()
    )

    if not chunks:
        logger.warning(f"No completed document chunks found for chapter {chapter_id}. Skipping knowledge profile generation.")
        return None

    full_text = "\n\n".join([c.chunk_text for c in chunks])

    # 2. Initialize Gemini GenAI Client
    api_key = os.environ.get("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", None)
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY is not configured.")

    client = genai.Client(api_key=api_key)

    prompt = (
        "You are an expert educational curriculum designer. Based on the following source materials "
        "extracted from a chapter's document upload(s), generate a structured knowledge profile "
        "consisting of: a chapter summary, learning objectives, key concepts, must-cover topics, and common misconceptions.\n\n"
        f"--- CHAPTER SOURCE MATERIALS ---\n{full_text}\n--- END SOURCE MATERIALS ---\n\n"
        "Provide your analysis matching the schema definition."
    )

    # Call Gemini with structured output configuration
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ChapterKnowledgeJSON,
        ),
    )

    # Validate response structure
    data = ChapterKnowledgeJSON.model_validate_json(response.text)

    # 3. Save to database
    knowledge = session.get(ChapterKnowledge, chapter_id)
    if not knowledge:
        knowledge = ChapterKnowledge(chapter_id=chapter_id)
        session.add(knowledge)

    knowledge.chapter_summary = data.chapter_summary
    knowledge.learning_objectives = data.learning_objectives
    knowledge.key_concepts = data.key_concepts
    knowledge.must_cover_topics = data.must_cover_topics
    knowledge.common_misconceptions = data.common_misconceptions
    knowledge.generated_at = datetime.utcnow()

    session.commit()
    session.refresh(knowledge)
    logger.info(f"Successfully generated chapter knowledge profile for chapter {chapter_id}")
    return knowledge


async def get_chapter_knowledge(db: AsyncSession, chapter_id: int) -> ChapterKnowledge | None:
    """
    Asynchronous function for FastAPI routes.
    Retrieves the ChapterKnowledge profile for a given chapter.
    """
    result = await db.execute(
        select(ChapterKnowledge).where(ChapterKnowledge.chapter_id == chapter_id)
    )
    return result.scalar_one_or_none()
