import logging
import os
import tempfile
import httpx
from datetime import datetime
from celery_worker import celery_app

logger = logging.getLogger(__name__)


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
    
    try:
        with Session() as session:
            from app.models.chapter_document import ChapterDocument
            from app.models.chapter_chunk import ChapterChunk
            from app.services.document_processor import extract_text_and_elements, chunk_document
            from app.services.s3_service import sign_url
            from google import genai

            # Get ChapterDocument record
            doc = session.get(ChapterDocument, document_id)
            if not doc:
                logger.error(f"ChapterDocument with ID {document_id} not found.")
                return

            # Update status to PROCESSING
            doc.processing_status = "PROCESSING"
            session.commit()

            # Sign S3 URL if necessary
            fresh_url = sign_url(doc.storage_url) or doc.storage_url
            
            # Download file from storage to temporary location
            suffix = os.path.splitext(doc.file_name)[-1].lower() or f".{doc.file_type.lower()}"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name
                
            logger.info(f"Downloading document {document_id} from {fresh_url} to {tmp_path}")
            with httpx.Client(follow_redirects=True, timeout=120) as client:
                r = client.get(fresh_url)
                r.raise_for_status()
                with open(tmp_path, "wb") as f:
                    f.write(r.content)

            # Extract elements & chunk text
            logger.info(f"Extracting elements from {tmp_path} (Type: {doc.file_type})")
            elements = extract_text_and_elements(tmp_path, doc.file_type)
            
            logger.info(f"Chunking elements for document {document_id}")
            chunks = chunk_document(elements, max_tokens=800, overlap_tokens=100)
            
            if not chunks:
                logger.warning(f"No text extracted/chunked from document {document_id}")
                doc.processing_status = "COMPLETED"
                session.commit()
                return

            # Initialize Google GenAI client for embeddings
            api_key = os.environ.get("GEMINI_API_KEY") or getattr(settings, "GEMINI_API_KEY", None)
            if not api_key:
                raise EnvironmentError("GEMINI_API_KEY is not configured.")
            
            genai_client = genai.Client(api_key=api_key)

            # Generate embeddings in batches (e.g. batch size of 32)
            batch_size = 32
            embedded_chunks = []
            
            logger.info(f"Generating embeddings for {len(chunks)} chunks of document {document_id}")
            for idx in range(0, len(chunks), batch_size):
                batch_texts = chunks[idx : idx + batch_size]
                
                response = genai_client.models.embed_content(
                    model="text-embedding-004",
                    contents=batch_texts
                )
                
                # Retrieve and pad embeddings
                # text-embedding-004 yields 768 dimensions. We pad to 1536 with 0s.
                for text, emb in zip(batch_texts, response.embeddings):
                    raw_vector = emb.values
                    padded_vector = raw_vector + [0.0] * (1536 - len(raw_vector))
                    embedded_chunks.append((text, padded_vector))

            # Delete any existing chunks for this document (re-processing safety)
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
        logger.error(
            "Document %d processing failed (attempt %d/%d): %s",
            document_id,
            self.request.retries + 1,
            self.max_retries + 1,
            exc,
            exc_info=True,
        )
        
        # Try to mark the document as FAILED if this is the final attempt
        is_final_attempt = self.request.retries >= self.max_retries
        try:
            with Session() as session:
                from app.models.chapter_document import ChapterDocument
                doc = session.get(ChapterDocument, document_id)
                if doc:
                    if is_final_attempt:
                        doc.processing_status = "FAILED"
                    session.commit()
        except Exception as db_exc:
            logger.error(
                "Failed to update document %d status to FAILED in DB: %s",
                document_id,
                db_exc,
                exc_info=True,
            )

        # Retry the Celery task with exponential backoff
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as e:
                logger.warning(f"Could not remove temp file {tmp_path}: {e}")


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

