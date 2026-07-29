import hashlib
import json
import logging
from sqlalchemy.orm import Session
from google import genai
from app.config import settings
from app.models.chapter_chunk import ChapterChunk
from app.models.chapter_knowledge import ChapterKnowledge
import redis

logger = logging.getLogger(__name__)

# Initialize Redis client
redis_client = None
if settings.REDIS_URL:
    try:
        redis_client = redis.from_url(settings.REDIS_URL)
    except Exception as e:
        logger.warning(f"Could not connect to Redis at {settings.REDIS_URL}: {e}")


def retrieve_chapter_context(session: Session, chapter_id: int, transcript: str) -> dict:
    """
    RAG service:
    1. Check Redis cache
    2. Embed transcript
    3. Search ChapterChunk table with pgvector similarity (top 10 chunks)
    4. Fetch ChapterKnowledge profile
    5. Cache result and return
    """
    if not transcript or not transcript.strip():
        logger.warning(f"Empty transcript passed to retrieve_chapter_context for chapter {chapter_id}")
        return {
            "chapter_summary": "",
            "learning_objectives": [],
            "key_concepts": [],
            "must_cover_topics": [],
            "retrieved_chunks": []
        }

    # Hash transcript to create a unique key
    tx_hash = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    cache_key = f"rag_context:{chapter_id}:{tx_hash}"

    if redis_client:
        try:
            cached_val = redis_client.get(cache_key)
            if cached_val:
                logger.info(f"RAG context Cache HIT for key: {cache_key}")
                return json.loads(cached_val)
        except Exception as e:
            logger.warning(f"Error checking cache: {e}")

    # Generate embedding
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY is not configured.")
    
    client = genai.Client(api_key=api_key)
    # text-embedding-004 token limit is ~2048, so we truncate if it's too long
    truncated_transcript = transcript[:8000]
    
    from google.genai import types

    response = client.models.embed_content(
        model="gemini-embedding-001",
        contents=truncated_transcript,
        config=types.EmbedContentConfig(
            output_dimensionality=1536
        )
    )
    
    raw_vector = response.embeddings[0].values
    padded_vector = raw_vector + [0.0] * (1536 - len(raw_vector))

    # Search ChapterChunk table
    # We use <=> for cosine distance in pgvector
    chunks = (
        session.query(ChapterChunk)
        .filter(ChapterChunk.chapter_id == chapter_id)
        .order_by(ChapterChunk.embedding_vector.op("<=>")(padded_vector))
        .limit(10)
        .all()
    )

    retrieved_chunks = [
        {
            "id": c.id,
            "chunk_text": c.chunk_text,
            "metadata_json": c.metadata_json
        }
        for c in chunks
    ]

    # Fetch ChapterKnowledge
    knowledge = session.get(ChapterKnowledge, chapter_id)
    
    result = {
        "chapter_summary": knowledge.chapter_summary if knowledge else "",
        "learning_objectives": knowledge.learning_objectives if knowledge else [],
        "key_concepts": knowledge.key_concepts if knowledge else [],
        "must_cover_topics": knowledge.must_cover_topics if knowledge else [],
        "retrieved_chunks": retrieved_chunks
    }

    # Save to Redis cache
    if redis_client:
        try:
            redis_client.setex(cache_key, 86400, json.dumps(result)) # Cache for 24 hours
            logger.info(f"RAG context Cache SET for key: {cache_key}")
        except Exception as e:
            logger.warning(f"Error setting cache: {e}")

    return result
