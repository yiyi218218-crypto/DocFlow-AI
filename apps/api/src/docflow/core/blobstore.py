"""Postgres-backed blob storage.

An alternative to S3/R2 selected with `STORAGE_BACKEND=postgres`.

**Why this exists.** Free hosting tiers give you a Postgres and nothing else —
no object store. Adding a fourth vendor just to hold a few megabytes of PDFs is
a worse trade than putting them in a table, so a free deployment runs on one
database and one web service.

**When NOT to use it.** Blobs in Postgres are genuinely bad at scale: they bloat
the table, slow logical backups, and every read costs a database round trip
instead of hitting a CDN. The moment storage passes a few GB or page images are
served at any volume, switch back with `STORAGE_BACKEND=s3`. The two backends
share an interface precisely so that is a config change.

Presigned URLs are emulated with a short-lived signed token, so the API surface
is identical: the caller is authorised first, then handed a URL.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import text

from docflow.core.config import settings
from docflow.core.database import system_session
from docflow.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size_bytes: int
    sha256: str


async def put_object(key: str, data: bytes, *, content_type: str) -> StoredObject:
    digest = hashlib.sha256(data).hexdigest()
    async with system_session() as session:
        await session.execute(
            text(
                """
                INSERT INTO blob_store (key, content_type, size_bytes, sha256, data)
                VALUES (:key, :ct, :size, :sha, :data)
                ON CONFLICT (key) DO UPDATE
                    SET content_type = EXCLUDED.content_type,
                        size_bytes   = EXCLUDED.size_bytes,
                        sha256       = EXCLUDED.sha256,
                        data         = EXCLUDED.data,
                        updated_at   = now()
                """
            ),
            {"key": key, "ct": content_type, "size": len(data), "sha": digest, "data": data},
        )
    return StoredObject(key=key, size_bytes=len(data), sha256=digest)


async def get_object(key: str) -> bytes:
    async with system_session() as session:
        row = await session.execute(
            text("SELECT data FROM blob_store WHERE key = :key"), {"key": key}
        )
        blob = row.scalar_one_or_none()
    if blob is None:
        raise FileNotFoundError(key)
    return bytes(blob)


async def get_object_meta(key: str) -> tuple[bytes, str] | None:
    async with system_session() as session:
        row = (
            await session.execute(
                text("SELECT data, content_type FROM blob_store WHERE key = :key"),
                {"key": key},
            )
        ).first()
    return (bytes(row[0]), row[1]) if row else None


async def delete_objects(keys: list[str]) -> None:
    if not keys:
        return
    async with system_session() as session:
        await session.execute(
            text("DELETE FROM blob_store WHERE key = ANY(:keys)"), {"keys": keys}
        )


async def list_prefix(prefix: str) -> list[str]:
    async with system_session() as session:
        rows = await session.execute(
            text("SELECT key FROM blob_store WHERE key LIKE :p"), {"p": f"{prefix}%"}
        )
        return [r[0] for r in rows]


async def promote(source_key: str, destination_key: str) -> None:
    async with system_session() as session:
        await session.execute(
            text(
                """
                INSERT INTO blob_store (key, content_type, size_bytes, sha256, data)
                SELECT :dst, content_type, size_bytes, sha256, data
                FROM blob_store WHERE key = :src
                ON CONFLICT (key) DO NOTHING
                """
            ),
            {"src": source_key, "dst": destination_key},
        )
        await session.execute(
            text("DELETE FROM blob_store WHERE key = :src"), {"src": source_key}
        )


async def check_storage() -> bool:
    try:
        async with system_session() as session:
            await session.execute(text("SELECT 1 FROM blob_store LIMIT 1"))
        return True
    except Exception:
        return False


async def ensure_bucket() -> None:
    """No-op: the table is created by a migration."""
    return None


# --- signed URLs ----------------------------------------------------------
# S3 presigning is replaced by an HMAC over (key, expiry). The endpoint that
# serves these verifies the signature, so the URL still expires and still
# cannot be forged — the caller is authorised before one is minted.


def _sign(key: str, expires_at: int) -> str:
    message = f"{key}:{expires_at}".encode()
    secret = settings.secret_key.get_secret_value().encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:32]


def presign_get(
    key: str,
    *,
    expires_in: int | None = None,
    download_filename: str | None = None,
    content_type: str | None = None,
) -> str:
    ttl = expires_in or settings.presign_ttl_seconds
    expires_at = int(time.time()) + ttl
    signature = _sign(key, expires_at)
    url = f"/api/v1/blobs/{key}?expires={expires_at}&signature={signature}"
    if download_filename:
        url += f"&filename={download_filename}"
    _ = content_type
    return url


def verify_signature(key: str, expires: int, signature: str) -> bool:
    if expires < time.time():
        return False
    return hmac.compare_digest(_sign(key, expires), signature)


# --- key layout (identical to the S3 backend) -----------------------------


def document_key(workspace_id: uuid.UUID, document_id: uuid.UUID, artifact: str) -> str:
    return f"ws/{workspace_id}/doc/{document_id}/{artifact}"


def quarantine_key(workspace_id: uuid.UUID, document_id: uuid.UUID, filename: str) -> str:
    safe = "".join(c for c in filename if c.isalnum() or c in "._-")[:100] or "upload"
    return f"quarantine/ws/{workspace_id}/{document_id}/{safe}"


def page_image_key(workspace_id: uuid.UUID, document_id: uuid.UUID, page: int) -> str:
    return document_key(workspace_id, document_id, f"pages/{page:04d}.webp")


async def delete_document_objects(workspace_id: uuid.UUID, document_id: uuid.UUID) -> int:
    keys = await list_prefix(f"ws/{workspace_id}/doc/{document_id}/")
    await delete_objects(keys)
    return len(keys)
