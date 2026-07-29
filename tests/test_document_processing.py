from unittest.mock import MagicMock, patch
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.chapter_document import ChapterDocument
from app.models.chapter_chunk import ChapterChunk
from app.models.chapter import Chapter


@pytest.mark.asyncio
async def test_upload_document_success(client: AsyncClient, school_admin_token, db_session: AsyncSession, subject):
    # 1. Create a chapter first
    chapter = Chapter(
        subject_id=subject.id,
        name="Introduction to Calculus",
        description="Limits and derivatives"
    )
    db_session.add(chapter)
    await db_session.commit()
    await db_session.refresh(chapter)

    # 2. Mock s3 upload & celery task
    with patch("app.routers.school_admin.upload_document") as mock_upload, \
         patch("app.tasks.document_tasks.process_chapter_document.delay") as mock_delay:
        
        mock_upload.return_value = {
            "url": "https://fake-s3-url.com/calculus.pdf",
            "public_id": "classecho/chapters/1/documents/fake-uuid.pdf",
            "file_type": "PDF"
        }

        # 3. Call endpoint
        file_payload = {"file": ("calculus.pdf", b"fake pdf content", "application/pdf")}
        response = await client.post(
            f"/school/chapters/{chapter.id}/documents",
            headers={"Authorization": school_admin_token},
            files=file_payload
        )

        assert response.status_code == 201
        data = response.json()
        assert data["file_name"] == "calculus.pdf"
        assert data["storage_url"] == "https://fake-s3-url.com/calculus.pdf"
        assert data["file_type"] == "PDF"
        assert data["processing_status"] == "PENDING"

        # Verify record exists in DB
        result = await db_session.execute(
            select(ChapterDocument).where(ChapterDocument.id == data["id"])
        )
        db_doc = result.scalar_one_or_none()
        assert db_doc is not None
        assert db_doc.chapter_id == chapter.id

        # Verify Celery task was triggered
        mock_delay.assert_called_once_with(db_doc.id)


@pytest.mark.asyncio
async def test_celery_task_processing(db_session: AsyncSession, subject):
    # Setup database elements
    chapter = Chapter(
        subject_id=subject.id,
        name="Limits",
        description="Limits introduction"
    )
    db_session.add(chapter)
    await db_session.commit()
    await db_session.refresh(chapter)

    doc = ChapterDocument(
        chapter_id=chapter.id,
        file_name="limits.txt",
        storage_url="https://fake-s3.com/limits.txt",
        file_type="TXT",
        processing_status="PENDING"
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    # Mock download, extraction and Google GenAI client
    fake_text_elements = [
        {"type": "heading", "text": "Section 1: Limits"},
        {"type": "paragraph", "text": "This is a paragraph about limits of functions. It describes how functions behave as x approaches a value."}
    ]
    
    fake_embedding_values = [0.1] * 768
    
    class FakeEmbedding:
        def __init__(self, values):
            self.values = values

    class FakeEmbedResponse:
        def __init__(self, embeddings):
            self.embeddings = embeddings

    mock_client = MagicMock()
    mock_client.models.embed_content.return_value = FakeEmbedResponse([FakeEmbedding(fake_embedding_values)])

    with patch("app.tasks.document_tasks.httpx.Client.get") as mock_get, \
         patch("app.tasks.document_tasks.extract_text_and_elements", return_value=fake_text_elements) as mock_extract, \
         patch("google.genai.Client", return_value=mock_client):

        # Mock downloading
        mock_response = MagicMock()
        mock_response.content = b"fake txt file content"
        mock_get.return_value = mock_response

        # Execute Celery task directly
        from app.tasks.document_tasks import process_chapter_document
        process_chapter_document(doc.id)

        # Refresh doc in DB to assert changes
        # Use sync session pattern or query the database since celery task updates DB directly
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.config import settings
        
        db_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
        engine = create_engine(db_url)
        Session = sessionmaker(bind=engine)
        
        with Session() as session:
            db_doc = session.get(ChapterDocument, doc.id)
            assert db_doc.processing_status == "COMPLETED"
            
            chunks = session.query(ChapterChunk).filter(ChapterChunk.document_id == doc.id).all()
            assert len(chunks) > 0
            # Check padding: 768 elements in mock embedding + 768 zeros = 1536 total dimensions
            assert len(chunks[0].embedding_vector) == 1536
            # Assert first 768 elements are 0.1, rest are 0.0
            assert chunks[0].embedding_vector[:768] == [0.1] * 768
            assert chunks[0].embedding_vector[768:] == [0.0] * 768
