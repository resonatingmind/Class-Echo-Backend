import os
import uuid
import tempfile
import boto3
from tinytag import TinyTag
from fastapi import UploadFile, HTTPException
from app.config import settings

from botocore.config import Config

# When object storage is not configured, files are written to ./uploads and
# served by the API itself. The Celery worker has to fetch them back over HTTP,
# so the host in that URL has to be reachable *from the worker* — not just from
# a browser. It defaults to the old hardcoded loopback address, which is right
# whenever the worker shares a container with the API.
INTERNAL_API_BASE_URL = (
    os.environ.get("INTERNAL_API_BASE_URL") or "http://127.0.0.1:8000"
).rstrip("/")

s3_client = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION,
    endpoint_url=f"https://s3.{settings.AWS_REGION}.amazonaws.com" if settings.AWS_REGION else None,
    config=Config(signature_version="s3v4"),
)

# The extension the audio is stored under decides which MIME type the analysis
# worker declares to Gemini. A browser recording arrives as webm; storing it as
# ".m4a" because the client happened to name it that made Gemini reject the file
# with "failed to process the audio". So trust the resolved content type first
# and only fall back to the client-supplied filename.
AUDIO_SUFFIX_BY_MIME = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/webm": ".webm",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
}

KNOWN_AUDIO_SUFFIXES = set(AUDIO_SUFFIX_BY_MIME.values())


def _audio_suffix(file: UploadFile, content_type: str | None) -> str:
    resolved = (content_type or file.content_type or "").split(";")[0].strip().lower()
    from_mime = AUDIO_SUFFIX_BY_MIME.get(resolved)
    if from_mime:
        return from_mime

    from_name = os.path.splitext(file.filename or "")[-1].lower()
    if from_name in KNOWN_AUDIO_SUFFIXES:
        return from_name

    return from_name or ".mp3"


async def upload_audio(
    file: UploadFile, subject_id: int, content_type: str | None = None
) -> dict:
    """Upload an audio file to AWS S3 and return url + public_id + duration."""
    contents = await file.read()

    # 1. Determine duration using tinytag
    suffix = _audio_suffix(file, content_type)
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

    # 2. Upload to S3 if configured, otherwise save locally
    if settings.AWS_STORAGE_BUCKET_NAME and settings.AWS_STORAGE_BUCKET_NAME != "test-bucket":
        file_uuid = uuid.uuid4().hex
        key = f"classecho/recordings/{subject_id}/{file_uuid}{suffix}"
        try:
            try:
                s3_client.put_object(
                    Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                    Key=key,
                    Body=contents,
                    ContentType=content_type or file.content_type or "audio/mpeg",
                    ACL="public-read"
                )
            except Exception:
                s3_client.put_object(
                    Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                    Key=key,
                    Body=contents,
                    ContentType=content_type or file.content_type or "audio/mpeg"
                )
        except Exception as exc:
            print("S3 UPLOAD ERROR:", exc)
            raise HTTPException(status_code=502, detail=f"Audio upload failed: {exc}")

        try:
            url = s3_client.generate_presigned_url(
                ClientMethod="get_object",
                Params={
                    "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
                    "Key": key,
                },
                ExpiresIn=604800,  # 7 days
            )
        except Exception as exc:
            print("S3 PRESIGN ERROR:", exc)
            url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/{key}"

        return {
            "url": url,
            "public_id": key,
            "duration": duration,
        }
    else:
        # Save locally
        file_uuid = uuid.uuid4().hex
        upload_dir = f"uploads/recordings/{subject_id}"
        os.makedirs(upload_dir, exist_ok=True)

        file_path = f"{upload_dir}/{file_uuid}{suffix}"

        with open(file_path, "wb") as f:
            f.write(contents)

        url = f"{INTERNAL_API_BASE_URL}/{file_path}"

        return {
            "url": url,
            "public_id": file_path,
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

    try:
        url = s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
                "Key": key,
            },
            ExpiresIn=604800,  # 7 days
        )
    except Exception as exc:
        print("S3 PRESIGN ERROR:", exc)
        url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/{key}"

    return {
        "url": url,
        "public_id": key,
    }

async def upload_document(file: UploadFile, folder: str) -> dict:
    """Upload a chapter document (PDF, DOCX, PPTX, TXT) to S3 and return url + public_id + file_type."""
    filename = file.filename or ""
    suffix = os.path.splitext(filename)[-1].lower()
    
    ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt"}
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {suffix}. Supported types: PDF, DOCX, PPTX, TXT"
        )
        
    contents = await file.read()
    file_uuid = uuid.uuid4().hex
    key = f"{folder}/{file_uuid}{suffix}"
    
    # Map extension to Content-Type
    MIME_TYPES = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt": "text/plain",
    }
    content_type = MIME_TYPES.get(suffix, "application/octet-stream")
    if settings.AWS_STORAGE_BUCKET_NAME and settings.AWS_STORAGE_BUCKET_NAME != "test-bucket":
        try:
            try:
                s3_client.put_object(
                    Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                    Key=key,
                    Body=contents,
                    ContentType=content_type,
                    ACL="public-read"
                )
            except Exception:
                s3_client.put_object(
                    Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                    Key=key,
                    Body=contents,
                    ContentType=content_type
                )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Document upload failed: {exc}")
            
        try:
            url = s3_client.generate_presigned_url(
                ClientMethod="get_object",
                Params={
                    "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
                    "Key": key,
                },
                ExpiresIn=604800,  # 7 days
            )
        except Exception as exc:
            print("S3 PRESIGN ERROR:", exc)
            url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/{key}"
            
        return {
            "url": url,
            "public_id": key,
            "file_type": suffix.strip(".").upper(),
        }
    else:
        # Save document locally instead of S3
        file_uuid = uuid.uuid4().hex

        upload_dir = f"uploads/{folder}"
        os.makedirs(upload_dir, exist_ok=True)

        file_path = f"{upload_dir}/{file_uuid}{suffix}"

        with open(file_path, "wb") as f:
            f.write(contents)

        url = f"{INTERNAL_API_BASE_URL}/{file_path}"

        return {
            "url": url,
            "public_id": file_path,
            "file_type": suffix.strip(".").upper(),
        }

def delete_audio(public_id: str) -> None:
    try:
        s3_client.delete_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=public_id
        )
    except Exception as exc:
        print("S3 DELETE ERROR:", exc)

def sign_url(url: str | None, expires_in: int = 604800) -> str | None:
    """If the URL is an AWS S3 URL, generate a fresh pre-signed URL for it."""
    if not url or "amazonaws.com/" not in url:
        return url
    try:
        # Extract the key from the URL.
        # Path-style URLs look like: https://s3.region.amazonaws.com/BUCKET/KEY?...
        # Virtual-hosted URLs look like: https://BUCKET.s3.region.amazonaws.com/KEY?...
        parts = url.split("amazonaws.com/")
        if len(parts) < 2:
            return url
        path = parts[1].split("?")[0]

        # If the path starts with the bucket name (path-style), strip it
        bucket = settings.AWS_STORAGE_BUCKET_NAME
        if path.startswith(f"{bucket}/"):
            key = path[len(f"{bucket}/"):]
        else:
            key = path

        if not key:
            return url

        # Generate new pre-signed URL
        return s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
            },
            ExpiresIn=expires_in,
        )
    except Exception as exc:
        print("S3 RE-SIGN ERROR:", exc)
        return url

