import os
import uuid
import tempfile
import boto3
from tinytag import TinyTag
from fastapi import UploadFile, HTTPException
from app.config import settings

from botocore.config import Config

s3_client = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION,
    endpoint_url=f"https://s3.{settings.AWS_REGION}.amazonaws.com" if settings.AWS_REGION else None,
    config=Config(signature_version="s3v4"),
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

