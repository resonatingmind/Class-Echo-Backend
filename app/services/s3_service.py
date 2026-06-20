import os
import uuid
import tempfile
import boto3
from tinytag import TinyTag
from fastapi import UploadFile, HTTPException
from app.config import settings

s3_client = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION,
)

async def upload_audio(file: UploadFile, subject_id: int) -> dict:
    """Upload an audio file to AWS S3 and return url + public_id + duration."""
    contents = await file.read()
    
    # 1. Determine duration using tinytag
    suffix = os.path.splitext(file.filename or "")[-1] or ".mp3"
    duration = None
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name
    try:
        tag = TinyTag.get(tmp_path)
        duration = tag.duration
    except Exception as exc:
        print("TINYTAG ERROR:", exc)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    # 2. Upload to S3
    file_uuid = uuid.uuid4().hex
    key = f"classecho/recordings/{subject_id}/{file_uuid}{suffix}"
    try:
        # Fall back to uploading without ACL if bucket restricts public ACLs
        try:
            s3_client.put_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=key,
                Body=contents,
                ContentType=file.content_type or "audio/mpeg",
                ACL="public-read"
            )
        except Exception:
            s3_client.put_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=key,
                Body=contents,
                ContentType=file.content_type or "audio/mpeg"
            )
    except Exception as exc:
        print("S3 UPLOAD ERROR:", exc)
        raise HTTPException(status_code=502, detail=f"Audio upload failed: {exc}")

    url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/{key}"
    return {
        "url": url,
        "public_id": key,
        "duration": duration,
    }

async def upload_image(file: UploadFile, folder: str) -> dict:
    """Upload an image to S3 and return url + public_id."""
    ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
    ct = (file.content_type or "").split(";")[0].strip().lower()
    if ct not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG, and WebP images are allowed")

    contents = await file.read()
    suffix = os.path.splitext(file.filename or "")[-1] or ".jpg"
    file_uuid = uuid.uuid4().hex
    key = f"{folder}/{file_uuid}{suffix}"

    try:
        try:
            s3_client.put_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=key,
                Body=contents,
                ContentType=file.content_type,
                ACL="public-read"
            )
        except Exception:
            s3_client.put_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=key,
                Body=contents,
                ContentType=file.content_type
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image upload failed: {exc}")

    url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/{key}"
    return {
        "url": url,
        "public_id": key,
    }

def delete_audio(public_id: str) -> None:
    try:
        s3_client.delete_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=public_id
        )
    except Exception as exc:
        print("S3 DELETE ERROR:", exc)
