"""Serve blobs held in Postgres.

Only reachable when STORAGE_BACKEND=postgres. It replaces the S3 presigned URL
with the same contract: the caller is authorised by whichever endpoint minted
the link, and the link itself carries an HMAC over (key, expiry) so it cannot be
forged and does expire.

The signature is checked here rather than the session, because these URLs are
handed to the browser's PDF fetch and image loads, which do not carry the
bearer token — exactly the constraint that made pdf.js fail against an
authenticated endpoint.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Response

from docflow.core import blobstore
from docflow.core.errors import NotFound
from docflow.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/blobs", tags=["blobs"])


@router.get("/{key:path}")
async def get_blob(
    key: str,
    expires: int = Query(...),
    signature: str = Query(...),
    filename: str | None = None,
) -> Response:
    if not blobstore.verify_signature(key, expires, signature):
        # 404 rather than 403: an expired or forged link should not confirm that
        # the underlying object exists.
        logger.info("blob.signature_rejected", key_prefix=key[:40])
        raise NotFound("This link is no longer valid.")

    found = await blobstore.get_object_meta(key)
    if found is None:
        raise NotFound("File not found.")

    data, content_type = found
    headers = {
        # Signed and short-lived, so it is safe to cache for its own lifetime.
        "Cache-Control": f"private, max-age={max(0, expires - int(__import__('time').time()))}",
    }
    if filename:
        safe = filename.replace('"', "")
        headers["Content-Disposition"] = f'attachment; filename="{safe}"'

    return Response(content=data, media_type=content_type, headers=headers)
