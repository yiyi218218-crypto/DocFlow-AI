"""The API, exercised as a client would.

Covers the full activation path — register, upload, process, read the analysis
back with highlight rectangles — plus the authorization and error-contract
behaviour that is easy to get subtly wrong and hard to notice.
"""

from __future__ import annotations

import uuid

import httpx
import pymupdf
import pytest
from httpx import ASGITransport

from docflow.main import app
from docflow.pipeline.orchestrator import process_document

pytestmark = pytest.mark.integration

CIRCULAR = """University of Dhaka
Admission Circular 2026-2027

Applications must be submitted by 15 September 2026.
Required documents include an academic transcript.
Applicants must also provide a copy of the National ID card.
"""


def build_pdf(text: str = CIRCULAR) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(50, 60, 545, 780), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
async def client():  # type: ignore[no-untyped-def]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def account(client):  # type: ignore[no-untyped-def]
    """A registered user with a workspace and an auth header."""
    email = f"user-{uuid.uuid4().hex[:10]}@example.com"
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-9", "full_name": "Test User"},
    )
    assert response.status_code == 201, response.text

    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "correct-horse-9"}
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    session = response.json()
    return {
        "email": email,
        "headers": {"Authorization": f"Bearer {token}"},
        "workspace_id": session["workspace"]["id"],
        "user": session["user"],
    }


# --------------------------------------------------------------------------
# system
# --------------------------------------------------------------------------


async def test_health_is_cheap_and_checks_nothing_external(client) -> None:
    """Liveness must not fail because a dependency blipped, or the orchestrator
    restarts a perfectly healthy process."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readiness_reports_each_dependency(client) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    checks = response.json()["checks"]
    assert checks == {"database": True, "redis": True, "storage": True}


async def test_security_headers_are_present(client) -> None:
    response = await client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


async def test_every_response_carries_a_correlation_id(client) -> None:
    response = await client.get("/health")
    assert response.headers["X-Request-ID"]


async def test_client_request_id_is_echoed_back(client) -> None:
    """The client's id wins, so a browser trace and a server log share a key."""
    response = await client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert response.headers["X-Request-ID"] == "trace-abc-123"


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


async def test_register_returns_a_session_and_a_workspace(client) -> None:
    email = f"new-{uuid.uuid4().hex[:10]}@example.com"
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-9", "full_name": "Nusrat Rahman"},
    )
    assert response.status_code == 201
    body = response.json()

    assert body["user"]["email"] == email
    assert body["role"] == "owner"
    assert "document:upload" in body["permissions"]
    assert body["workspace"]["name"] == "Nusrat's Workspace"


async def test_duplicate_registration_is_rejected(client, account) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": account["email"], "password": "correct-horse-9"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "EMAIL_ALREADY_REGISTERED"


async def test_weak_password_is_rejected_with_field_errors(client) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": f"w-{uuid.uuid4().hex[:8]}@example.com", "password": "short"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert any(e["field"] == "password" for e in body["errors"])


async def test_wrong_password_is_rejected(client, account) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": account["email"], "password": "definitely-not-it-9"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"


async def test_login_sets_an_httponly_refresh_cookie(client, account) -> None:
    """The refresh token must never be readable by JavaScript."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": account["email"], "password": "correct-horse-9"},
    )
    assert response.status_code == 200
    assert "docflow_refresh" not in response.json()

    cookie = response.headers.get("set-cookie", "")
    assert "docflow_refresh=" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/api/v1/auth" in cookie


async def test_refresh_rotates_the_token(client, account) -> None:
    first = await client.post(
        "/api/v1/auth/login",
        json={"email": account["email"], "password": "correct-horse-9"},
    )
    original_cookie = client.cookies.get("docflow_refresh")

    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"] != first.json()["access_token"]
    assert client.cookies.get("docflow_refresh") != original_cookie


async def test_forgot_password_never_reveals_whether_an_account_exists(client, account) -> None:
    """Any other behaviour turns this into an enumeration oracle (T14)."""
    known = await client.post("/api/v1/auth/forgot-password", json={"email": account["email"]})
    unknown = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "nobody@example.com"}
    )
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()


async def test_unauthenticated_request_is_a_problem_document(client) -> None:
    response = await client.get("/api/v1/documents")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "UNAUTHENTICATED"
    assert body["request_id"]
    assert "type" in body and "title" in body


# --------------------------------------------------------------------------
# the activation path
# --------------------------------------------------------------------------


async def test_upload_returns_202_and_queues_the_document(client, account) -> None:
    response = await client.post(
        "/api/v1/documents",
        headers=account["headers"],
        files={"file": ("circular.pdf", build_pdf(), "application/pdf")},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["poll_url"] == f"/api/v1/documents/{body['id']}"


async def test_full_upload_to_analysis_flow(client, account) -> None:
    """Upload, process, and read the analysis back with working citations.

    This is the activation journey from docs/00-product.md §4 J2, asserted end
    to end through the HTTP API.
    """
    upload = await client.post(
        "/api/v1/documents",
        headers=account["headers"],
        files={"file": ("circular.pdf", build_pdf(), "application/pdf")},
    )
    document_id = upload.json()["id"]

    # The worker is not running under test, so drive the pipeline directly.
    await process_document(uuid.UUID(document_id), uuid.UUID(account["workspace_id"]))

    detail = await client.get(f"/api/v1/documents/{document_id}", headers=account["headers"])
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"
    assert len(detail.json()["stages"]) == 11
    assert detail.headers.get("ETag")

    analysis = await client.get(
        f"/api/v1/documents/{document_id}/analysis", headers=account["headers"]
    )
    assert analysis.status_code == 200
    body = analysis.json()

    assert body["facts"], "no facts returned"
    assert body["citation_verification_rate"] is not None
    assert body["counts_by_kind"]

    deadlines = [f for f in body["facts"] if f["kind"] == "deadline"]
    assert deadlines, "the circular states a deadline"

    deadline = deadlines[0]
    assert deadline["due_at"].startswith("2026-09-15")
    assert deadline["citation_status"] in ("verified", "partial")
    assert deadline["source_page"] == 1

    # The rectangles are what make click-to-source work at all.
    assert deadline["source_rects"], "no highlight rectangle"
    rect = deadline["source_rects"][0]
    assert 0.0 <= rect["x0"] < rect["x1"] <= 1.0
    assert 0.0 <= rect["y0"] < rect["y1"] <= 1.0

    # Page images are presigned so the viewer can render immediately.
    assert body["pages"][0]["image_url"]


async def test_duplicate_upload_returns_the_existing_document(client, account) -> None:
    """Re-uploading the same circular costs nothing and points at the original."""
    pdf = build_pdf()
    first = await client.post(
        "/api/v1/documents",
        headers=account["headers"],
        files={"file": ("circular.pdf", pdf, "application/pdf")},
    )
    second = await client.post(
        "/api/v1/documents",
        headers=account["headers"],
        files={"file": ("same-circular.pdf", pdf, "application/pdf")},
    )

    assert second.status_code == 409
    body = second.json()
    assert body["code"] == "DUPLICATE_DOCUMENT"
    assert body["duplicate_of"] == first.json()["id"]


async def test_document_list_is_scoped_and_paginated(client, account) -> None:
    for i in range(3):
        await client.post(
            "/api/v1/documents",
            headers=account["headers"],
            files={"file": (f"doc-{i}.pdf", build_pdf(f"{CIRCULAR}\nCopy {i}"), "application/pdf")},
        )

    response = await client.get("/api/v1/documents?limit=2", headers=account["headers"])
    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 2
    assert body["has_more"] is True
    assert body["next_cursor"]

    page_two = await client.get(
        f"/api/v1/documents?limit=2&cursor={body['next_cursor']}",
        headers=account["headers"],
    )
    assert page_two.status_code == 200
    first_ids = {d["id"] for d in body["data"]}
    second_ids = {d["id"] for d in page_two.json()["data"]}
    assert not (first_ids & second_ids), "cursor pagination returned a duplicate row"


# --------------------------------------------------------------------------
# tenant isolation, through the API
# --------------------------------------------------------------------------


async def test_another_tenants_document_is_a_404_not_a_403(client, account) -> None:
    """403 would confirm the id exists. 404 reveals nothing (T6)."""
    upload = await client.post(
        "/api/v1/documents",
        headers=account["headers"],
        files={"file": ("mine.pdf", build_pdf(), "application/pdf")},
    )
    document_id = upload.json()["id"]

    other_email = f"other-{uuid.uuid4().hex[:10]}@example.com"
    await client.post(
        "/api/v1/auth/register",
        json={"email": other_email, "password": "correct-horse-9"},
    )
    other_login = await client.post(
        "/api/v1/auth/login", json={"email": other_email, "password": "correct-horse-9"}
    )
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    response = await client.get(f"/api/v1/documents/{document_id}", headers=other_headers)
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_forged_workspace_header_is_rejected(client, account) -> None:
    """The header may only narrow an already-authorised set, never widen it."""
    response = await client.get(
        "/api/v1/documents",
        headers={**account["headers"], "X-Workspace-Id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


async def test_missing_document_is_404(client, account) -> None:
    response = await client.get(f"/api/v1/documents/{uuid.uuid4()}", headers=account["headers"])
    assert response.status_code == 404


# --------------------------------------------------------------------------
# upload validation
# --------------------------------------------------------------------------


async def test_unsupported_file_fails_during_processing_with_a_clear_message(
    client, account
) -> None:
    upload = await client.post(
        "/api/v1/documents",
        headers=account["headers"],
        files={"file": ("payload.bin", b"\x00\x01\x02not a document", "application/octet-stream")},
    )
    assert upload.status_code == 202  # accepted, then rejected by the pipeline

    document_id = upload.json()["id"]
    await process_document(uuid.UUID(document_id), uuid.UUID(account["workspace_id"]))

    detail = await client.get(f"/api/v1/documents/{document_id}", headers=account["headers"])
    body = detail.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "INVALID_FILE_TYPE"


async def test_rate_limit_headers_are_returned(client, account) -> None:
    response = await client.get("/api/v1/documents", headers=account["headers"])
    assert response.headers.get("RateLimit-Limit")
    assert response.headers.get("RateLimit-Remaining")
