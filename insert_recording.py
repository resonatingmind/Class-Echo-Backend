from app.database import engine
from sqlalchemy import text
import asyncio

async def test():
    try:
        async with engine.begin() as conn:
            # Check the recordings table schema first
            columns_query = await conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'recordings'"))
            print("Recordings table columns:")
            for col in columns_query.fetchall():
                print(f"  {col[0]}: {col[1]}")

            # Insert a recording with lecture_title and transcription
            await conn.execute(
                text(
                    "INSERT INTO recordings (subject_id, teacher_id, chapter_name, lecture_title, description, transcription, cloudinary_url, cloudinary_public_id, status) "
                    "VALUES (23, 23, 'Chapter 5: Advanced S3', 'Lecture 1: Presigned URLs V4', 'S3 setup description', 'Here is the transcript for S3 setup.', 'https://example.com/s3.mp3', 's3_public_id', 'completed')"
                )
            )
            print("Successfully inserted recording.")
            
            # Fetch it back
            res = await conn.execute(text("SELECT id, chapter_name, lecture_title, transcription FROM recordings ORDER BY id DESC LIMIT 1"))
            print("Fetched record:", res.fetchone())
            
    except Exception as e:
        print("Failed:", e)

asyncio.run(test())
