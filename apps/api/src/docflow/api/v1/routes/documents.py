"""Document endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from fastapi.responses import RedirectResponse

from docflow.api.v1.deps import CurrentPrincipal, TenantSession, require
from docflow.core import storage
from docflow.core.config import settings
from docflow.core.errors import DuplicateDocument, NotFound
from docflow.core.permissions import Permission, can_delete_document
from docflow.models.enums import DocumentStatus, FactKind, PipelineStage
from docflow.schemas.common import Page
from docflow.schemas.documents import (
    DocumentAnalysisOut,
    DocumentDetail,
    DocumentSummary,
    FactOut,
    PageOut,
    StageOut,
    UploadAccepted,
)
from docflow.services import document_service

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    response_model=UploadAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.DOCUMENT_UPLOAD))],
)
async def upload_document(
    principal: CurrentPrincipal,
    session: TenantSession,
    file: Annotated[UploadFile, File()],
) -> UploadAccepted:
    """Accept a document and queue it.

    202 rather than 201: processing takes 20-120 s, so the response describes a
    job that has started, not a resource that is ready. The client polls
    `poll_url` (SSE in Phase 2).

    Multipart rather than presigned direct-to-storage: at a 25 MB cap the extra
    two endpoints and abandoned-upload reconciliation buy nothing. See
    docs/30-data-api.md §13.2 for the switch trigger.
    """
    content = await file.read()

    try:
        document, _ = await document_service.create_document(
            session,
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            filename=file.filename or "document",
            content=content,
            declared_mime=file.content_type,
        )
    except DuplicateDocument as exc:
        # Surface the existing id so the client can navigate straight to it
        # instead of showing an error the user cannot act on.
        raise exc

    from docflow.worker.queue import enqueue_document

    await enqueue_document(document.id, principal.workspace_id)

    return UploadAccepted(
        id=document.id,
        status=document.status,
        original_filename=document.original_filename,
        size_bytes=document.size_bytes,
        created_at=document.created_at,
        poll_url=f"/api/v1/documents/{document.id}",
    )


@router.get("", response_model=Page[DocumentSummary])
async def list_documents(
    principal: CurrentPrincipal,
    session: TenantSession,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    status_filter: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    q: str | None = None,
) -> Page[DocumentSummary]:
    documents, next_cursor, has_more = await document_service.list_documents(
        session,
        workspace_id=principal.workspace_id,
        cursor=cursor,
        limit=limit,
        status=status_filter,
        query=q,
    )

    counts = await document_service.fact_counts(session, document_ids=[d.id for d in documents])

    data = []
    for document in documents:
        summary = DocumentSummary.model_validate(document)
        extra = counts.get(document.id, {})
        summary.fact_count = extra.get("fact_count", 0)
        summary.deadline_count = extra.get("deadline_count", 0)
        summary.next_deadline_at = extra.get("next_deadline_at")
        data.append(summary)

    return Page[DocumentSummary](data=data, next_cursor=next_cursor, has_more=has_more)


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: TenantSession,
    response: Response,
) -> DocumentDetail:
    """Document plus its stage rows, so the processing view is one request."""
    document = await document_service.get_document(
        session, workspace_id=principal.workspace_id, document_id=document_id
    )

    detail = DocumentDetail.model_validate(document)
    detail.stages = [
        StageOut.model_validate(s)
        for s in sorted(document.stages, key=lambda s: PipelineStage(s.stage).index)
    ]

    # A completed document's representation is immutable until reprocessing, so
    # an ETag here genuinely saves round trips.
    if document.status.is_terminal:
        response.headers["ETag"] = f'W/"{document.id}-{document.updated_at.timestamp()}"'
    else:
        # Tell the client roughly how soon to poll again.
        response.headers["Retry-After"] = "2"

    return detail


@router.get("/{document_id}/analysis", response_model=DocumentAnalysisOut)
async def get_analysis(
    document_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: TenantSession,
    include_low_confidence: bool = False,
    kinds: Annotated[list[FactKind] | None, Query()] = None,
) -> DocumentAnalysisOut:
    """Everything the detail page renders, in one round trip."""
    result = await document_service.get_analysis(
        session,
        workspace_id=principal.workspace_id,
        document_id=document_id,
        include_low_confidence=include_low_confidence,
        kinds=kinds,
    )

    image_urls = await document_service.page_image_urls(result["document"], result["pages"])
    pages = []
    for page in result["pages"]:
        out = PageOut.model_validate(page)
        out.image_url = image_urls.get(page.page_number)
        pages.append(out)

    detail = DocumentDetail.model_validate(result["document"])
    detail.stages = [
        StageOut.model_validate(s)
        for s in sorted(result["document"].stages, key=lambda s: PipelineStage(s.stage).index)
    ]

    return DocumentAnalysisOut(
        document=detail,
        facts=[FactOut.model_validate(f) for f in result["facts"]],
        pages=pages,
        counts_by_kind=result["counts_by_kind"],
        confidence_summary=result["confidence_summary"],
        citation_verification_rate=result["citation_verification_rate"],
        low_confidence_hidden=result["low_confidence_hidden"],
    )


@router.get("/{document_id}/file")
async def get_document_file(
    document_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: TenantSession,
    original: bool = False,
) -> RedirectResponse:
    """Redirect to a short-lived presigned URL.

    Authorisation happens here, before the URL exists — the URL is the result of
    an access decision, never the mechanism for one.
    """
    document = await document_service.get_document(
        session, workspace_id=principal.workspace_id, document_id=document_id
    )

    key = document.storage_key_original if original else document.storage_key_canonical
    if not key:
        raise NotFound("This document has not finished processing yet.")

    url = storage.presign_get(
        key,
        expires_in=settings.presign_ttl_seconds,
        download_filename=document.original_filename if original else None,
        content_type="application/pdf",
    )
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> None:
    document = await document_service.get_document(
        session, workspace_id=principal.workspace_id, document_id=document_id
    )
    if not can_delete_document(
        principal.role, is_uploader=document.uploaded_by == principal.user_id
    ):
        principal.require(Permission.DOCUMENT_DELETE_ANY)

    await document_service.soft_delete(
        session, workspace_id=principal.workspace_id, document_id=document_id
    )


@router.post(
    "/{document_id}/reprocess",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.DOCUMENT_REPROCESS))],
)
async def reprocess_document(
    document_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: TenantSession,
    from_stage: PipelineStage = PipelineStage.EXTRACTING,
) -> dict[str, object]:
    """Re-run from a chosen stage.

    Completed earlier stages are skipped by their idempotency keys, so
    reprocessing after a prompt change costs one stage rather than the document.
    """
    document = await document_service.get_document(
        session, workspace_id=principal.workspace_id, document_id=document_id
    )

    from docflow.pipeline.orchestrator import reset_stages_from
    from docflow.worker.queue import enqueue_document

    reset = await reset_stages_from(session, document.id, from_stage)
    document.status = DocumentStatus.QUEUED
    document.error_code = None
    document.error_detail = None
    await session.flush()

    await enqueue_document(document.id, principal.workspace_id)
    return {"status": "queued", "stages_reset": reset, "from_stage": from_stage.value}
