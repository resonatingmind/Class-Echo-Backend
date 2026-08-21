"""
Cloudinary storage backend.

Mirrors the upload helpers in ``s3_service`` so the two are interchangeable —
``s3_service`` picks between them based on ``settings.storage_backend``, which
keeps every call site and the ``cloudinary_url`` / ``cloudinary_public_id``
columns unchanged.

Two Cloudinary specifics drive the shape of this module:

* Audio has no resource type of its own — it is uploaded as ``video``. Using
  ``image`` (the library default) fails for m4a/webm.
* Delivery URLs are public and permanent, so there is nothing to re-sign. That
  removes the expiring pre-signed URL problem the S3 path has, where a stored
  link went stale after seven days.
"""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.config import settings

logger = logging.getLogger(__name__)

_configured = False

# Cloudinary has no audio resource type — audio is uploaded as "video", and
# documents as "raw". Only raw resources keep their extension in the public_id;
# for the other two Cloudinary appends the format it detected.
AUDIO_RESOURCE_TYPE = "video"
DOCUMENT_RESOURCE_TYPE = "raw"
IMAGE_RESOURCE_TYPE = "image"


def _public_id(path: str) -> str:
    """
    Prefix a public_id with the configured Cloudinary folder.

    Cloudinary folders are just public_id prefixes, so this keeps every asset
    under one namespace instead of scattering them at the account root.
    """
    folder = (settings.CLOUDINARY_FOLDER or "").strip().strip("/")
    return f"{folder}/{path}" if folder else path


def _configure() -> None:
    """Configure the SDK once, from either credential style."""
    global _configured
    if _configured:
        return

    import cloudinary

    if settings.CLOUDINARY_URL:
        # The SDK reads CLOUDINARY_URL from the environment itself, but only if
        # it is actually present there — pydantic may have loaded it from .env
        # into settings without exporting it.
        os.environ.setdefault("CLOUDINARY_URL", settings.CLOUDINARY_URL)
        cloudinary.config(secure=True)
    else:
        cloudinary.config(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            secure=True,
        )

    _configured = True


def _upload_sync(file_obj, *, public_id: str, resource_type: str) -> dict:
    """
    Blocking upload. Called via run_in_threadpool so a large lecture does not
    stall the event loop for every other request.

    ``upload_large`` chunks the transfer, which matters for hour-long lectures:
    the plain ``upload`` call has a much lower single-request size ceiling.
    """
    import cloudinary.uploader

    return cloudinary.uploader.upload_large(
        file_obj,
        public_id=public_id,
        resource_type=resource_type,
        chunk_size=20_000_000,
        overwrite=True,
        invalidate=True,
    )


async def _upload(
    file: UploadFile,
    *,
    public_id: str,
    resource_type: str,
    what: str,
) -> dict:
    _configure()

    # Starlette already spooled the body to a temp file on disk, so hand the
    # file object straight to Cloudinary rather than reading it into memory.
    # `await file.read()` on the S3 path holds the whole recording in RAM.
    try:
        await file.seek(0)
    except Exception:
        pass

    try:
        return await run_in_threadpool(
            _upload_sync,
            file.file,
            public_id=public_id,
            resource_type=resource_type,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Cloudinary %s upload failed", what)
        raise HTTPException(
            status_code=502, detail=f"{what} upload failed: {exc}"
        ) from exc


async def upload_audio(
    file: UploadFile, subject_id: int, content_type: str | None = None
) -> dict:
    """Upload a lecture recording. Returns url + public_id + duration."""
    # No extension in the public_id: Cloudinary appends the format it actually
    # detected for video/audio resources, so including one yields ".m4a.m4a".
    # Letting Cloudinary decide is also more accurate than the client's guess —
    # the delivery URL ends up with the real container either way, which is what
    # the analysis worker reads to pick a MIME type.
    public_id = _public_id(f"recordings/{subject_id}/{uuid.uuid4().hex}")

    result = await _upload(
        file,
        public_id=public_id,
        resource_type=AUDIO_RESOURCE_TYPE,
        what="Audio",
    )

    return {
        "url": result.get("secure_url") or result.get("url"),
        "public_id": result.get("public_id") or public_id,
        # Cloudinary probes the media itself, which is more reliable than the
        # tinytag pass the S3 path does — and costs nothing extra.
        "duration": result.get("duration"),
    }


async def upload_image(file: UploadFile, folder: str) -> dict:
    ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
    ct = (file.content_type or "").split(";")[0].strip().lower()
    if ct not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400, detail="Only JPEG, PNG, and WebP images are allowed"
        )

    public_id = _public_id(f"{folder}/{uuid.uuid4().hex}")
    result = await _upload(
        file,
        public_id=public_id,
        resource_type=IMAGE_RESOURCE_TYPE,
        what="Image",
    )

    return {
        "url": result.get("secure_url") or result.get("url"),
        "public_id": result.get("public_id") or public_id,
    }


async def upload_document(file: UploadFile, folder: str) -> dict:
    filename = file.filename or ""
    suffix = os.path.splitext(filename)[-1].lower()

    ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt"}
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {suffix}. Supported types: PDF, DOCX, PPTX, TXT",
        )

    # Raw resources are served verbatim, so the extension must be part of the
    # public_id or the download has no type.
    public_id = _public_id(f"{folder}/{uuid.uuid4().hex}{suffix}")
    result = await _upload(
        file,
        public_id=public_id,
        resource_type=DOCUMENT_RESOURCE_TYPE,
        what="Document",
    )

    return {
        "url": result.get("secure_url") or result.get("url"),
        "public_id": result.get("public_id") or public_id,
        "file_type": suffix.strip(".").upper(),
    }


def delete_audio(public_id: str) -> None:
    _configure()
    import cloudinary.uploader

    try:
        cloudinary.uploader.destroy(
            public_id, resource_type=AUDIO_RESOURCE_TYPE, invalidate=True
        )
    except Exception:
        logger.exception("Cloudinary delete failed for %s", public_id)


def is_cloudinary_url(url: str | None) -> bool:
    return bool(url) and "res.cloudinary.com" in url
