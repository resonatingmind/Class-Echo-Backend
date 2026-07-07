import json
from unittest.mock import MagicMock, patch
import pytest
from httpx import AsyncClient

from app.database import AsyncSessionLocal
from app.config import settings
from app.models.chapter import Chapter
from app.models.chapter_document import ChapterDocument
from app.models.chapter_chunk import ChapterChunk
from app.models.chapter_knowledge import ChapterKnowledge


class FakeGeminiResponse:
    def __init__(self, text: str):
        self.text = text


@pytest.mark.asyncio
async def test_chapter_knowledge_generation_and_api(client: AsyncClient, school_admin_token, school_admin_user, subject):
    chapter_id = None
    doc_id = None

    # 1. Isolated DB Setup using a fresh AsyncSession
    async with AsyncSessionLocal() as session:
        chapter = Chapter(
            subject_id=subject.id,
            name="Introduction to Calculus",
            description="Limits and derivatives"
        )
        session.add(chapter)
        await session.commit()
        await session.refresh(chapter)
        chapter_id = chapter.id

        doc = ChapterDocument(
            chapter_id=chapter.id,
            file_name="calculus.txt",
            storage_url="https://fake-s3-url.com/calculus.txt",
            file_type="TXT",
            processing_status="COMPLETED"
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        doc_id = doc.id

        chunk = ChapterChunk(
            chapter_id=chapter.id,
            document_id=doc.id,
            chunk_text="Calculus is the mathematical study of continuous change. Limits are the foundation.",
            embedding_vector=[0.1] * 3072
        )
        session.add(chunk)
        await session.commit()

    # 2. Assert getting non-existent knowledge returns 404
    resp = await client.get(
        f"/school/chapters/{chapter_id}/knowledge",
        headers={"Authorization": school_admin_token}
    )
    assert resp.status_code == 404

    # 3. Mock the Gemini response JSON matching the schema
    gemini_json_response = {
        "chapter_summary": "Introduction to continuous change and limits.",
        "learning_objectives": ["Understand limits", "Calculate basic derivatives"],
        "key_concepts": ["Limits", "Continuity", "Derivatives"],
        "must_cover_topics": ["Epsilon-delta limit", "Rate of change"],
        "common_misconceptions": ["Thinking limits must be reached"]
    }

    mock_genai_client = MagicMock()
    mock_genai_client.models.generate_content.return_value = FakeGeminiResponse(
        json.dumps(gemini_json_response)
    )

    # 4. Trigger the Celery task directly
    with patch("google.genai.Client", return_value=mock_genai_client):
        from app.tasks.document_tasks import generate_chapter_knowledge_task
        generate_chapter_knowledge_task(chapter_id)

    # 5. Check the GET API returns the generated profile
    resp = await client.get(
        f"/school/chapters/{chapter_id}/knowledge",
        headers={"Authorization": school_admin_token}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["chapter_id"] == chapter_id
    assert data["chapter_summary"] == "Introduction to continuous change and limits."
    assert data["learning_objectives"] == ["Understand limits", "Calculate basic derivatives"]

    # 6. Check manual regeneration POST endpoint works
    with patch("app.tasks.document_tasks.generate_chapter_knowledge_task.delay") as mock_delay:
        resp = await client.post(
            f"/school/chapters/{chapter_id}/knowledge/regenerate",
            headers={"Authorization": school_admin_token}
        )
        assert resp.status_code == 202
        assert resp.json() == {"message": "Chapter knowledge generation task started."}
        mock_delay.assert_called_once_with(chapter_id)
