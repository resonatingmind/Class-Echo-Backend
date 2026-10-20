import logging
import os
import tempfile
import httpx
from datetime import datetime
from celery_worker import celery_app

logger = logging.getLogger(__name__)

ERROR_REASON_MAX_CHARS = 500


class DocumentProcessingError(Exception):
    """A failure with a message already written for the person who uploaded the file."""

    def __init__(self, reason: str, *, transient: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.transient = transient


def _is_transient(exc: Exception) -> bool:
    """Would the same attempt plausibly succeed if run again later?

    Only these are retried. Everything else — a refused download, a corrupt
    file, a missing API key — fails the same way every time, and retrying it
    just left the document showing "processing" for ~15 minutes before it
    finally reported FAILED.
    """
    if isinstance(exc, DocumentProcessingError):
        return exc.transient
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code in (408, 429) or code >= 500
    if isinstance(exc, httpx.TransportError):
        return True
    try:
        from google.genai import errors as genai_errors

        if isinstance(exc, genai_errors.ServerError):
            return True
        if isinstance(exc, genai_errors.ClientError):
            return getattr(exc, "code", None) == 429
    except ImportError:
        pass
    from sqlalchemy.exc import OperationalError

    return isinstance(exc, OperationalError)


def _describe(stage: str, exc: Exception, file_type: str | None) -> str:
    """Turn an exception into a reason a teacher or admin can act on."""
    if isinstance(exc, DocumentProcessingError):
        return exc.reason
    if stage == "download":
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            reason = f"The file could not be downloaded from storage (HTTP {code})."
            if code in (401, 403):
                reason += " Storage refused access to it; check the storage account's delivery settings."
            elif code == 404:
                reason += " It may have been deleted from storage."
            return reason
        return f"The file could not be downloaded from storage: {exc}"
    if stage == "extract":
        return f"The {file_type or 'uploaded'} file could not be read. It may be corrupt or password-protected. ({exc})"
    if stage == "embed":
        return f"The AI service could not index this document: {exc}"
    return f"Processing failed: {exc}"


def _download_url(storage_url: str) -> str:
    from app.services import cloudinary_service
    from app.services.s3_service import sign_url

    if cloudinary_service.is_cloudinary_url(storage_url):
        try:
            signed = cloudinary_service.signed_download_url(storage_url)
            if signed:
                return signed
        except Exception as exc:
            logger.warning("Could not sign Cloudinary download, using delivery URL: %s", exc)
    return sign_url(storage_url) or storage_url


@celery_app.task(bind=True, max_retries=5)
def process_chapter_document(self, document_id: int):
    """
    Celery task to process a ChapterDocument:
    1. Extract text
    2. Chunk content (heading-aware, fallback 800 tokens, 100 tokens overlap)
    3. Generate embeddings using Google's text-embedding-004
    4. Pad embeddings to 1536 dimensions (to fit db schema)
    5. Save chunks and embeddings to database
    6. Update status (PENDING -> PROCESSING -> COMPLETED/FAILED)

    Only transient failures are retried; anything else marks the document
    FAILED straight away with error_reason saying why.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.config import settings

    # Synchronous SQLAlchemy Engine/Session to avoid async complexity in Celery
    db_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)

    # Temporary file storage
    tmp_path = None
    stage = "start"
    file_type = None

    try:
        with Session() as session:
            from app.models.chapter_document import ChapterDocument
            from app.models.chapter_chunk import ChapterChunk
            from app.services.document_processor import extract_text_and_elements, chunk_document
            from google import genai

            # Get ChapterDocument record
            doc = session.get(ChapterDocument, document_id)
            if not doc:
                logger.error(f"ChapterDocument with ID {document_id} not found.")
                return
            file_type = doc.file_type

            # Update status to PROCESSING
            doc.processing_status = "PROCESSING"
            doc.error_reason = None
            session.commit()

            # Download file from storage to temporary location
            stage = "download"
            suffix = os.path.splitext(doc.file_name)[-1].lower() or f".{doc.file_type.lower()}"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name

            logger.info(f"Downloading document {document_id} from {doc.storage_url} to {tmp_path}")
            with httpx.Client(follow_redirects=True, timeout=120) as client:
                r = client.get(_download_url(doc.storage_url))
                r.raise_for_status()
                with open(tmp_path, "wb") as f:
                    f.write(r.content)

            # Extract elements & chunk text
            stage = "extract"
            logger.info(f"Extracting elements from {tmp_path} (Type: {doc.file_type})")
            elements = extract_text_and_elements(tmp_path, doc.file_type)

            logger.info(f"Chunking elements for document {document_id}")
            chunks = chunk_document(elements, max_tokens=800, overlap_tokens=100)

            if not chunks:
                # Used to be marked COMPLETED, which showed a document that
                # contributes nothing to the chapter as successfully indexed.
                raise DocumentProcessingError(
                    "No readable text was found in this file. Scanned PDFs "
                    "(pages saved as images) are not supported; upload a "
                    "version with selectable text."
                )

            # Initialize Google GenAI client for embeddings
            stage = "embed"
            api_key = os.environ.get("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", None)
            if not api_key:
                raise DocumentProcessingError(
                    "The server has no GEMINI_API_KEY configured, so documents cannot be indexed."
                )

            genai_client = genai.Client(api_key=api_key)

            # Generate embeddings in batches (e.g. batch size of 32)
            batch_size = 32
            embedded_chunks = []

            logger.info(f"Generating embeddings for {len(chunks)} chunks of document {document_id}")
            for idx in range(0, len(chunks), batch_size):
                batch_texts = chunks[idx : idx + batch_size]

                from google.genai import types

                response = genai_client.models.embed_content(
                    model="gemini-embedding-001",
                    contents=batch_texts,
                    config=types.EmbedContentConfig(
                        output_dimensionality=1536
                    )
                )

                # Retrieve and pad embeddings
                # text-embedding-004 yields 768 dimensions. We pad to 1536 with 0s.
                for text, emb in zip(batch_texts, response.embeddings):
                    raw_vector = emb.values
                    padded_vector = raw_vector + [0.0] * (1536 - len(raw_vector))
                    embedded_chunks.append((text, padded_vector))

            # Delete any existing chunks for this document (re-processing safety)
            stage = "save"
            session.query(ChapterChunk).filter(ChapterChunk.document_id == document_id).delete()

            # Store chunks in database
            for text, vector in embedded_chunks:
                chunk = ChapterChunk(
                    chapter_id=doc.chapter_id,
                    document_id=doc.id,
                    chunk_text=text,
                    embedding_vector=vector,
                    metadata_json={}
                )
                session.add(chunk)

            # Update document status to COMPLETED
            doc.processing_status = "COMPLETED"
            session.commit()
            logger.info(f"Successfully processed document {document_id}")

            # Trigger Chapter Knowledge profile generation task
            generate_chapter_knowledge_task.delay(doc.chapter_id)

    except Exception as exc:
        transient = _is_transient(exc)
        is_final_attempt = not transient or self.request.retries >= self.max_retries
        logger.error(
            "Document %d processing failed at %s (attempt %d/%d, %s): %s",
            document_id,
            stage,
            self.request.retries + 1,
            self.max_retries + 1,
            "will retry" if not is_final_attempt else "giving up",
            exc,
            exc_info=True,
        )

        if is_final_attempt:
            reason = _describe(stage, exc, file_type)
            if transient:
                reason = f"Gave up after {self.request.retries + 1} attempts. {reason}"
            try:
                with Session() as session:
                    from app.models.chapter_document import ChapterDocument
                    doc = session.get(ChapterDocument, document_id)
                    if doc:
                        doc.processing_status = "FAILED"
                        doc.error_reason = reason[:ERROR_REASON_MAX_CHARS]
                        session.commit()
            except Exception as db_exc:
                logger.error(
                    "Failed to update document %d status to FAILED in DB: %s",
                    document_id,
                    db_exc,
                    exc_info=True,
                )
            # Recorded on the document; re-raising would only make Celery log it twice.
            return

        # Retry the Celery task with exponential backoff
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as e:
                logger.warning(f"Could not remove temp file {tmp_path}: {e}")
        # A new engine per task; without this each run leaves its pool's
        # connections open against the (connection-limited) Supabase pooler.
        engine.dispose()


@celery_app.task(bind=True, max_retries=3)
def generate_chapter_knowledge_task(self, chapter_id: int):
    """
    Celery task to generate or update the ChapterKnowledge profile for a chapter.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.config import settings
    from app.services.chapter_knowledge_service import generate_chapter_knowledge

    db_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)

    try:
        with Session() as session:
            generate_chapter_knowledge(session, chapter_id)
    except Exception as exc:
        logger.error(
            "ChapterKnowledge generation for chapter %d failed (attempt %d/%d): %s",
            chapter_id,
            self.request.retries + 1,
            self.max_retries + 1,
            exc,
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))
