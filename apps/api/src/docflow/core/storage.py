"""S3-compatible object storage.

MinIO locally, Cloudflare R2 in production — one `S3_ENDPOINT_URL` switches them
and the code is byte-identical. R2 over S3 is not a marginal call for this
product: the viewer re-fetches the same PDF and its page images on every open, so
egress dominates, and R2 charges nothing for it.

Access is never by key guessing. The API authorises first, then mints a
short-lived presigned URL. No bucket is ever publicly readable.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, BinaryIO

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from docflow.core import blobstore
from docflow.core.config import settings
from docflow.core.logging import get_logger

logger = get_logger(__name__)

# Files land here first and are promoted only after validation passes, so an
# unvalidated upload is never reachable through a normal document key.
QUARANTINE_PREFIX = "quarantine"


def _use_postgres() -> bool:
    return settings.storage_backend == "postgres"


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size_bytes: int
    sha256: str


@lru_cache(maxsize=1)
def _client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            # R2 rejects the checksum headers boto3 added by default in 2025.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


# --- key layout -----------------------------------------------------------
# ws/{workspace}/doc/{document}/... — tenant-prefixed so a bucket listing is
# still segmented even though listing is never exposed.


def document_key(workspace_id: uuid.UUID, document_id: uuid.UUID, artifact: str) -> str:
    return f"ws/{workspace_id}/doc/{document_id}/{artifact}"


def quarantine_key(workspace_id: uuid.UUID, document_id: uuid.UUID, filename: str) -> str:
    safe = "".join(c for c in filename if c.isalnum() or c in "._-")[:100] or "upload"
    return f"{QUARANTINE_PREFIX}/ws/{workspace_id}/{document_id}/{safe}"


def page_image_key(workspace_id: uuid.UUID, document_id: uuid.UUID, page: int) -> str:
    return document_key(workspace_id, document_id, f"pages/{page:04d}.webp")


# --- operations -----------------------------------------------------------


def _put_sync(key: str, data: bytes, content_type: str) -> StoredObject:
    _client().put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
        # Never inline: an uploaded file rendered in the origin would be a stored
        # XSS primitive (T3/T9).
        ContentDisposition="attachment",
    )
    return StoredObject(key=key, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


async def put_object(
    key: str, data: bytes, *, content_type: str = "application/octet-stream"
) -> StoredObject:
    """boto3 is synchronous; keep it off the event loop."""
    if _use_postgres():
        result = await blobstore.put_object(key, data, content_type=content_type)
        return StoredObject(key=result.key, size_bytes=result.size_bytes, sha256=result.sha256)
    return await asyncio.to_thread(_put_sync, key, data, content_type)


def _get_sync(key: str) -> bytes:
    response = _client().get_object(Bucket=settings.s3_bucket, Key=key)
    return bytes(response["Body"].read())


async def get_object(key: str) -> bytes:
    if _use_postgres():
        return await blobstore.get_object(key)
    return await asyncio.to_thread(_get_sync, key)


def _copy_sync(source: str, destination: str) -> None:
    _client().copy_object(
        Bucket=settings.s3_bucket,
        CopySource={"Bucket": settings.s3_bucket, "Key": source},
        Key=destination,
    )
    _client().delete_object(Bucket=settings.s3_bucket, Key=source)


async def promote(source_key: str, destination_key: str) -> None:
    """Move a validated upload out of quarantine."""
    if _use_postgres():
        await blobstore.promote(source_key, destination_key)
        return
    await asyncio.to_thread(_copy_sync, source_key, destination_key)


def _delete_sync(keys: list[str]) -> None:
    if not keys:
        return
    _client().delete_objects(
        Bucket=settings.s3_bucket,
        Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True},
    )


async def delete_objects(keys: list[str]) -> None:
    if _use_postgres():
        await blobstore.delete_objects(keys)
        return
    await asyncio.to_thread(_delete_sync, keys)


def _list_sync(prefix: str) -> list[str]:
    paginator = _client().get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix):
        keys.extend(item["Key"] for item in page.get("Contents", []))
    return keys


async def list_prefix(prefix: str) -> list[str]:
    if _use_postgres():
        return await blobstore.list_prefix(prefix)
    return await asyncio.to_thread(_list_sync, prefix)


async def delete_document_objects(workspace_id: uuid.UUID, document_id: uuid.UUID) -> int:
    """Hard-delete every artifact for a document. Used by the retention purge."""
    keys = await list_prefix(f"ws/{workspace_id}/doc/{document_id}/")
    await delete_objects(keys)
    return len(keys)


def presign_get(
    key: str,
    *,
    expires_in: int | None = None,
    download_filename: str | None = None,
    content_type: str | None = None,
) -> str:
    """Mint a short-lived read URL.

    Authorisation happens in the API *before* this is called — the URL is the
    result of an access decision, never the mechanism for one. Never logged (the
    structlog redaction list covers presigned_url).
    """
    if _use_postgres():
        return blobstore.presign_get(
            key,
            expires_in=expires_in,
            download_filename=download_filename,
            content_type=content_type,
        )

    params: dict[str, Any] = {"Bucket": settings.s3_bucket, "Key": key}
    if download_filename:
        safe = download_filename.replace('"', "")
        params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
    if content_type:
        params["ResponseContentType"] = content_type

    return str(
        _client().generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires_in or settings.presign_ttl_seconds,
        )
    )


def _ensure_bucket_sync() -> None:
    client = _client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket)
        logger.info("storage.bucket_created", bucket=settings.s3_bucket)


async def ensure_bucket() -> None:
    """Create the bucket if missing. Local convenience; a no-op in production."""
    if _use_postgres():
        return
    await asyncio.to_thread(_ensure_bucket_sync)


async def check_storage() -> bool:
    if _use_postgres():
        return await blobstore.check_storage()
    try:
        await asyncio.to_thread(lambda: _client().head_bucket(Bucket=settings.s3_bucket))
        return True
    except Exception:
        return False


def compute_sha256(stream: BinaryIO, *, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    """Hash while streaming, so a 25 MB upload is never fully buffered twice."""
    digest = hashlib.sha256()
    total = 0
    while chunk := stream.read(chunk_size):
        digest.update(chunk)
        total += len(chunk)
    stream.seek(0)
    return digest.hexdigest(), total
