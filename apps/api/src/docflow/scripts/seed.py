"""Seed the demo workspace.

Deliberately makes **zero LLM calls**. Everything runs through the real pipeline
with the stub provider, so `make demo` gives a working, populated app on a clean
machine with no API keys, offline, in about a minute.

That property is doing three jobs at once:
  * a recruiter can clone the repo and see the product, not a README;
  * the hackathon demo does not depend on venue wifi or provider rate limits;
  * the E2E suite has deterministic fixtures.

Run with: make seed
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pymupdf
from sqlalchemy import select

from docflow.core import storage
from docflow.core.config import settings
from docflow.core.database import system_session, tenant_session
from docflow.core.logging import configure_logging, get_logger
from docflow.core.security import hash_password
from docflow.models.document import Document, DocumentFact
from docflow.models.enums import (
    DocumentStatus,
    FactKind,
    TaskOrigin,
    TaskPriority,
    TaskStatus,
    WorkspaceRole,
)
from docflow.models.identity import Membership, User, Workspace
from docflow.models.work import Task
from docflow.pipeline.orchestrator import ensure_stage_rows, process_document

logger = get_logger(__name__)

DEMO_EMAIL = "demo@docflow.ai"
DEMO_PASSWORD = "demo-workspace-2026"  # noqa: S105 - a published demo credential

SAMPLE_DOCUMENTS: list[tuple[str, str]] = [
    (
        "du-admission-circular-2026.pdf",
        """University of Dhaka
Office of the Registrar

ADMISSION CIRCULAR 2026-2027
Reference: DU/REG/ADM/2026/114
Published on 1 August 2026

Applications for the 2026-2027 undergraduate session must be submitted by
15 September 2026 at 5:00 PM.

Required documents
Applicants must submit an attested copy of the academic transcript.
Applicants must also provide a photocopy of the National ID card.
Two passport-size photographs are required with the application form.
A bank draft of BDT 1000 must be attached as the application fee.

Eligibility
Candidates must have completed HSC or an equivalent examination in 2024 or later.
Candidates must hold a minimum GPA of 3.50 in both SSC and HSC.

Important notes
Late submissions will not be considered under any circumstances.
Incomplete applications will be rejected without further notice.

For queries contact admission@du.ac.bd or call 02-9661900.
Further details are available at https://admission.du.ac.bd
""",
    ),
    (
        "prime-minister-scholarship-2026.pdf",
        """Ministry of Education
Government of the People's Republic of Bangladesh

PRIME MINISTER'S SCHOLARSHIP PROGRAMME 2026
Reference: MOE/SCH/2026/07
Published on 12 August 2026

The online application portal closes on 30 September 2026.
Shortlisted candidates will be interviewed on 20 October 2026.

Applicants must submit a certified copy of the most recent academic transcript.
Applicants must provide an income certificate issued by the Union Parishad.
A recommendation letter from the head of the institution is required.

Eligibility
The applicant must be a citizen of Bangladesh.
The applicant must be enrolled in a public university in the current session.
The annual family income must not exceed BDT 300000.

Selected scholars receive a monthly stipend of BDT 5000 for one academic year.
Applications submitted after the deadline will not be reviewed.

Contact the scholarship cell at scholarship@moedu.gov.bd for assistance.
""",
    ),
    (
        "internship-notice-cse.pdf",
        """Department of Computer Science and Engineering

INTERNSHIP PLACEMENT NOTICE - SPRING 2026
Published on 5 August 2026

Students intending to join the industrial internship programme must register
by 25 August 2026.

Registration requirements
Students must submit the completed internship registration form.
Students must attach a current CV of no more than two pages.
A no-objection letter from the academic advisor is required.

Students must have completed at least 100 credit hours to be eligible.
The placement interview will be held on 1 September 2026.

Students who miss the registration deadline must wait until the next semester.

For queries email internship@cse.edu.bd
""",
    ),
]


def build_pdf(body: str) -> bytes:
    """Render seed text as a real PDF so the pipeline parses it for real.

    Injecting rows directly would skip parsing, alignment and confidence — the
    parts most likely to break — and the demo would prove nothing.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(50, 55, 545, 790), body, fontsize=10.5, lineheight=1.35)
    data = doc.tobytes()
    doc.close()
    return data


async def ensure_demo_account() -> tuple[uuid.UUID, uuid.UUID]:
    """Create (or find) the demo user and workspace."""
    async with system_session() as session:
        user = await session.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user is None:
            user = User(
                email=DEMO_EMAIL,
                password_hash=hash_password(DEMO_PASSWORD),
                full_name="Demo User",
                email_verified_at=datetime.now(UTC),
                timezone="Asia/Dhaka",
            )
            session.add(user)
            await session.flush()

        workspace = await session.scalar(
            select(Workspace).where(Workspace.slug == settings.demo_workspace_slug)
        )
        if workspace is None:
            workspace = Workspace(
                name="Demo Workspace",
                slug=settings.demo_workspace_slug,
                owner_id=user.id,
                timezone="Asia/Dhaka",
            )
            session.add(workspace)
            await session.flush()
            session.add(
                Membership(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OWNER)
            )

        return user.id, workspace.id


async def seed_document(
    workspace_id: uuid.UUID, user_id: uuid.UUID, filename: str, body: str
) -> uuid.UUID | None:
    """Upload and fully process one sample document."""
    import hashlib

    pdf = build_pdf(body)
    sha = hashlib.sha256(pdf).hexdigest()

    async with tenant_session(workspace_id, user_id) as session:
        existing = await session.scalar(
            select(Document).where(
                Document.workspace_id == workspace_id,
                Document.content_sha256 == sha,
                Document.deleted_at.is_(None),
            )
        )
        if existing is not None:
            logger.info("seed.document_exists", filename=filename)
            return existing.id

        document_id = uuid.uuid4()
        key = storage.quarantine_key(workspace_id, document_id, filename)
        await storage.put_object(key, pdf, content_type="application/pdf")

        document = Document(
            id=document_id,
            workspace_id=workspace_id,
            uploaded_by=user_id,
            original_filename=filename,
            mime_type="application/pdf",
            size_bytes=len(pdf),
            content_sha256=sha,
            storage_key_original=key,
            status=DocumentStatus.QUEUED,
        )
        session.add(document)
        await session.flush()
        await ensure_stage_rows(session, document)

    summary = await process_document(document_id, workspace_id)
    if "failed_stage" in summary:
        logger.error("seed.document_failed", filename=filename, summary=summary)
        return None

    logger.info("seed.document_ready", filename=filename)
    return document_id


async def seed_tasks(workspace_id: uuid.UUID, user_id: uuid.UUID) -> int:
    """Generate tasks from the highest-confidence facts.

    Every AI task keeps its source_fact_id, so the demo can show the provenance
    chain running all the way from a task back to a highlighted span.
    """
    created = 0
    async with tenant_session(workspace_id, user_id) as session:
        existing = await session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.origin == TaskOrigin.AI)
        )
        if existing is not None:
            return 0

        facts = (
            (
                await session.execute(
                    select(DocumentFact)
                    .where(
                        DocumentFact.workspace_id == workspace_id,
                        DocumentFact.kind.in_([FactKind.DEADLINE, FactKind.REQUIREMENT]),
                    )
                    .order_by(DocumentFact.confidence_calibrated.desc())
                    .limit(12)
                )
            )
            .scalars()
            .all()
        )

        for fact in facts:
            if fact.kind is FactKind.DEADLINE:
                title = f"Submit: {fact.payload.get('label', 'application')}"
                due = fact.due_at
                priority = TaskPriority.HIGH
            else:
                description = str(fact.payload.get("description", ""))[:120]
                title = f"Obtain: {description}"
                # Documents take time to gather, so back-date off the deadline.
                due = None
                priority = TaskPriority.MEDIUM

            session.add(
                Task(
                    workspace_id=workspace_id,
                    document_id=fact.document_id,
                    source_fact_id=fact.id,
                    title=title[:500],
                    status=TaskStatus.TODO,
                    priority=priority,
                    due_at=due,
                    origin=TaskOrigin.AI,
                    created_by=user_id,
                    sort_order=created,
                )
            )
            created += 1

        # One completed and one upcoming manual task, so the demo dashboard is
        # not uniformly "todo".
        session.add(
            Task(
                workspace_id=workspace_id,
                title="Open a bank account for the application fee",
                status=TaskStatus.DONE,
                completed_at=datetime.now(UTC) - timedelta(days=2),
                priority=TaskPriority.LOW,
                origin=TaskOrigin.USER,
                created_by=user_id,
                sort_order=created + 1,
            )
        )
    return created


async def main() -> None:
    configure_logging(level="INFO", json_output=False, service="seed")
    logger.info("seed.starting", demo_mode=settings.effective_demo_mode)

    await storage.ensure_bucket()
    user_id, workspace_id = await ensure_demo_account()

    processed = 0
    for filename, body in SAMPLE_DOCUMENTS:
        if await seed_document(workspace_id, user_id, filename, body):
            processed += 1

    tasks = await seed_tasks(workspace_id, user_id)

    logger.info("seed.completed", documents=processed, tasks=tasks)
    print()
    print(f"  Demo workspace ready: {processed} documents, {tasks} generated tasks")
    print(f"  Sign in with  {DEMO_EMAIL}  /  {DEMO_PASSWORD}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
