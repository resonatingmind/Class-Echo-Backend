import json
import hashlib
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import AsyncSessionLocal
from app.config import settings
from app.models.chapter import Chapter
from app.models.chapter_document import ChapterDocument
from app.models.chapter_chunk import ChapterChunk
from app.models.chapter_knowledge import ChapterKnowledge
from app.models.recording import Recording
from app.services.rag_service import retrieve_chapter_context, redis_client
from app.tasks.llm_tasks import process_recording


class FakeGeminiResponse:
    def __init__(self, text: str):
        self.text = text


class FakeEmbedding:
    def __init__(self, values: list[float]):
        self.values = values


class FakeEmbedResponse:
    def __init__(self, embeddings: list):
        self.embeddings = embeddings


class FakeState:
    def __init__(self, name="ACTIVE"):
        self.name = name


class FakeFile:
    def __init__(self, name="fake_file"):
        self.name = name
        self.state = FakeState()


import json
import hashlib
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.orm import sessionmaker

from app.database import engine
from app.models.chapter import Chapter
from app.models.chapter_document import ChapterDocument
from app.models.chapter_chunk import ChapterChunk
from app.models.chapter_knowledge import ChapterKnowledge
from app.models.recording import Recording
from app.services.rag_service import retrieve_chapter_context, redis_client
from app.tasks.llm_tasks import process_recording


class FakeGeminiResponse:
    def __init__(self, text: str):
        self.text = text


class FakeEmbedding:
    def __init__(self, values: list[float]):
        self.values = values


class FakeEmbedResponse:
    def __init__(self, embeddings: list):
        self.embeddings = embeddings


class FakeState:
    def __init__(self, name="ACTIVE"):
        self.name = name


class FakeFile:
    def __init__(self, name="fake_file"):
        self.name = name
        self.state = FakeState()


@pytest.mark.asyncio
async def test_retrieve_chapter_context(db_session, subject):
    chapter = Chapter(
        subject_id=subject.id,
        name="RAG Chapter",
        description="Chapter for RAG tests"
    )
    db_session.add(chapter)
    await db_session.commit()
    await db_session.refresh(chapter)

    doc = ChapterDocument(
        chapter_id=chapter.id,
        file_name="rag_doc.txt",
        storage_url="https://fake-s3-url.com/rag_doc.txt",
        file_type="TXT",
        processing_status="COMPLETED"
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    chunk = ChapterChunk(
        chapter_id=chapter.id,
        document_id=doc.id,
        chunk_text="This is a test chunk containing information about machine learning.",
        embedding_vector=[0.1] * 1536
    )
    db_session.add(chunk)

    knowledge = ChapterKnowledge(
        chapter_id=chapter.id,
        chapter_summary="Summary of Machine Learning chapter.",
        learning_objectives=["Understand supervised learning"],
        key_concepts=["Features", "Labels"],
        must_cover_topics=["Linear Regression"],
        common_misconceptions=["Thinking correlation is causation"]
    )
    db_session.add(knowledge)
    await db_session.commit()

    # Clear redis cache for clean test run
    if redis_client:
        try:
            tx_hash = hashlib.sha256(b"Machine learning is fun").hexdigest()
            redis_client.delete(f"rag_context:{chapter.id}:{tx_hash}")
        except Exception:
            pass

    # Mock Gemini Client and Embedding call
    mock_genai_client = MagicMock()
    mock_genai_client.models.embed_content.return_value = FakeEmbedResponse(
        [FakeEmbedding([0.1] * 768)]
    )

    # Sync session wrapper using the app's sync engine
    SyncSession = sessionmaker(bind=engine.sync_engine)

    with patch("google.genai.Client", return_value=mock_genai_client):
        with SyncSession() as sync_session:
            context = retrieve_chapter_context(
                session=sync_session,
                chapter_id=chapter.id,
                transcript="Machine learning is fun"
            )

            assert context["chapter_summary"] == "Summary of Machine Learning chapter."
            assert "supervised learning" in context["learning_objectives"][0]
            assert len(context["retrieved_chunks"]) == 1
            assert context["retrieved_chunks"][0]["chunk_text"] == "This is a test chunk containing information about machine learning."

            # Verify client embed_content was called
            mock_genai_client.models.embed_content.assert_called_once_with(
                model="text-embedding-004",
                contents="Machine learning is fun"
            )


@pytest.mark.asyncio
async def test_process_recording_with_rag(db_session, subject, teacher_user):
    chapter = Chapter(
        subject_id=subject.id,
        name="Calculus RAG",
        description="Calculus derivatives"
    )
    db_session.add(chapter)
    await db_session.commit()
    await db_session.refresh(chapter)

    doc = ChapterDocument(
        chapter_id=chapter.id,
        file_name="calculus.txt",
        storage_url="https://fake-s3-url.com/calculus.txt",
        file_type="TXT",
        processing_status="COMPLETED"
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    chunk = ChapterChunk(
        chapter_id=chapter.id,
        document_id=doc.id,
        chunk_text="Derivative represents the rate of change.",
        embedding_vector=[0.05] * 1536
    )
    db_session.add(chunk)

    knowledge = ChapterKnowledge(
        chapter_id=chapter.id,
        chapter_summary="Summary of derivatives.",
        learning_objectives=["Calculate derivative"],
        key_concepts=["Derivative"],
        must_cover_topics=["Power rule"],
        common_misconceptions=["Thinking limit is value"]
    )
    db_session.add(knowledge)

    recording = Recording(
        subject_id=subject.id,
        chapter_id=chapter.id,
        teacher_id=teacher_user.id,
        cloudinary_url="https://fake-s3.com/audio.mp3",
        cloudinary_public_id="audio_id",
        status="pending",
        transcription=None  # Missing transcription, triggers transcribe
    )
    db_session.add(recording)
    await db_session.commit()
    await db_session.refresh(recording)

    # Mock the Gemini responses
    mock_genai_client = MagicMock()
    mock_genai_client.files.upload.return_value = FakeFile()
    mock_genai_client.files.get.return_value = FakeFile()
    mock_genai_client.models.embed_content.return_value = FakeEmbedResponse(
        [FakeEmbedding([0.05] * 768)]
    )
    mock_genai_client.models.generate_content.side_effect = [
        # First call is transcription
        FakeGeminiResponse("Today we will learn about derivatives."),
        # Second call is evaluation
        FakeGeminiResponse(
            json.dumps({
                "scores": {
                    "verbal_clarity": {"score": 18, "finding": "Clear voice", "evidence": ["~00:10 — 'clear'"]},
                    "pacing_delivery": {"score": 13, "finding": "Good pace", "evidence": ["~00:20 — 'pace'"]},
                    "content_structure": {"score": 14, "finding": "Well structured", "evidence": ["~00:30 — 'structure'"]},
                    "conceptual_depth": {"score": 19, "finding": "Accurate concepts", "evidence": ["~00:40 — 'concepts'"]},
                    "student_engagement": {"score": 14, "finding": "Interactive", "evidence": ["~00:50 — 'questions'"]},
                    "language_accessibility": {"score": 9, "finding": "Good language", "evidence": ["~01:00 — 'accessibility'"]},
                    "closure_recap": {"score": 5, "finding": "Good summary", "evidence": ["~01:10 — 'summary'"]}
                },
                "total": 92,
                "quantitative": {
                    "wpm_estimate": 140,
                    "filler_words_heard": 5,
                    "questions_asked": 3,
                    "languages_detected": ["English"],
                    "code_switching_frequency": "low"
                },
                "overall_notes": "Very effective lecture utilizing derivative concepts.",
                "top_strengths": ["Clear voice", "Interactive style"],
                "priority_improvements": ["Increase practice examples"]
            })
        )
    ]

    # Mock local download file presence
    with patch("app.services.llm._download_audio", return_value="fake_local_audio.wav"):
        with patch("os.path.exists", return_value=True):
            with patch("os.remove") as mock_remove:
                with patch("google.genai.Client", return_value=mock_genai_client):
                    # Trigger the Celery task directly
                    process_recording(recording.id, "https://fake-s3.com/audio.mp3")

    # Verify recording updated in DB
    await db_session.refresh(recording)
    assert recording.status == "completed"
    assert recording.transcription == "Today we will learn about derivatives."
    
    # Reload and assert report details
    from sqlalchemy.orm import selectinload
    from sqlalchemy import select
    res = await db_session.execute(
        select(Recording)
        .options(selectinload(Recording.report))
        .where(Recording.id == recording.id)
    )
    recording_with_report = res.scalar_one()
    assert recording_with_report.report is not None
    assert recording_with_report.report.overall_score == 92


