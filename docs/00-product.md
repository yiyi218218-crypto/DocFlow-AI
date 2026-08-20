# DocFlow AI — Part 0: Product Blueprint

> Sections 1–7: Executive summary, vision, users, journeys, differentiation, MVP, roadmap.
> Read [10-architecture.md](10-architecture.md) next.

---

## 1. Executive Summary

**DocFlow AI turns documents into decisions, deadlines, tasks, and actions.**

The market is saturated with "chat with your PDF" products. They all fail the same way: the user still has to read the answer, decide what it means, and remember to act. DocFlow's thesis is that **the chat interface is the least valuable part of document AI**. The valuable part is the deterministic pipeline that converts an unstructured document into a *typed, verifiable, actionable* object graph — deadlines with real dates, requirements with checkboxes, tasks with owners — where every single field can be traced back to the pixel it came from.

### The one thing that must be right

Everything in this blueprint serves a single invariant:

> **The Provenance Chain**
> `document → page → character span → bounding box → extracted fact → generated task`
>
> Every extracted fact carries a verified quote from the source. If we cannot verify the quote against the source text, we do not show the fact as confirmed.

This is not a nice-to-have. It is the product, the trust model, the primary evaluation metric, and the anti-hallucination defense. It is also the thing that makes the UI demo memorable: *click a deadline → the PDF scrolls to page 3 and highlights the exact sentence.*

### Ten decisions that define this architecture

| # | Decision | Why it matters |
|---|---|---|
| 1 | **Normalize every input to PDF + page image + word-level text map** | One viewer, one citation model, one highlight implementation — for DOCX, TXT, images, and PDFs alike. Removes ~4 parallel code paths. |
| 2 | **Citations are computed, not trusted** | The LLM returns a quote; deterministic fuzzy alignment maps it to character offsets → word boxes → rectangles. Unmatched quotes are flagged, never rendered as confirmed. |
| 3 | **Full-document context first, RAG second** | A 10-page circular fits in context. RAG exists for workspace-wide search and long documents, not as the default path. Cheaper *and* more accurate. |
| 4 | **Dates are normalized by code, not by the LLM** | The LLM extracts the raw string; `dateparser` + Bangla digit transliteration produces the timestamp. LLMs are unreliable at DD/MM ambiguity and relative dates. |
| 5 | **Confidence is computed, not self-reported** | LLM self-confidence is poorly calibrated. We compute a composite from citation-match score, OCR confidence, and cross-window agreement, then calibrate it on the eval set. |
| 6 | **One `document_facts` table, not five** | Deadlines/requirements/entities/action items/risks share ~80% of their columns (provenance, confidence, review state). One table = one review UI, one citation path. |
| 7 | **Postgres RLS as a backstop under app-level tenant filtering** | Belt and braces against the #1 SaaS breach class: the forgotten `WHERE workspace_id = ?`. |
| 8 | **Pipeline state lives in Postgres; the queue is only delivery** | Gives durable-execution semantics (resume from stage N, exactly-once per stage) without adopting Temporal. Makes the queue library swappable in ~200 lines. |
| 9 | **No LangChain. Thin native adapters behind a tier-based LLM protocol.** | ~300 lines of adapters buys full control of token accounting, caching, retries and cost attribution — which is the whole cost-control story. |
| 10 | **Document content is untrusted input with zero capability** | The extraction call has no tools, no network, no DB. A prompt injection can at worst produce a wrong extraction — never an action. |

### What "done" looks like

A recruiter or investor opening the GitHub repo sees: a README with a 20-second GIF of the citation click-through, a `docs/EVALUATION.md` with **real measured F1/Recall@k numbers** on a hand-labeled dataset, ADRs explaining the trade-offs above, a green CI badge covering lint/type/test/migration-drift/security-scan, and a live demo with a guest workspace requiring no signup.

---

## 2. Product Vision

### Positioning statement

> For students, professionals, and small teams who are drowning in circulars, notices, contracts, and policies, DocFlow AI is a document intelligence workspace that extracts the deadlines, requirements, and actions buried in your documents and turns them into a tracked to-do list — with every fact traceable to its source line.
>
> Unlike Chat-with-PDF tools, DocFlow does not stop at answering questions. It produces *state you can act on*.

### The three-layer product

```
Layer 3  ACT      Tasks, reminders, calendar, notifications, ICS feed
                  ↑ the layer competitors don't have
Layer 2  DECIDE   Deadlines, requirements, risks, eligibility, decisions
                  ↑ typed, verified, confidence-scored
Layer 1  UNDERSTAND  Classification, extraction, entities, summary, grounded Q&A
                  ↑ where everyone else stops
```

Most competitors ship Layer 1 and call it a product. Layer 2 is where the engineering difficulty lives (verification, calibration, normalization). Layer 3 is where the retention lives — a user who has tasks and reminders in your product comes back; a user who asked your chatbot a question does not.

### Long-term vision (3 years)

DocFlow becomes the **obligation graph** for an organization: every document that creates a duty, a deadline, or a dependency is ingested, and the system maintains a live, queryable model of *what this organization owes to whom, by when, and what happens if it slips*. Contracts, invoices, compliance policies, procurement notices, HR documents — the extraction schemas differ, the pipeline does not.

### Non-goals (deliberately)

- **Not a document editor.** No WYSIWYG, no redlining, no e-signature. Those are separate, crowded products.
- **Not a DMS.** We are not competing with SharePoint/Google Drive on storage. We integrate with them later.
- **Not a general-purpose agent.** No autonomous email sending, no browsing, no code execution on the user's behalf. Capability isolation is a security position, not a limitation.
- **Not a translation product.** Bangla support is about *understanding* Bangla documents, not translating them.

---

## 3. Target Users

### Wedge: Bangladeshi students and applicants (0–12 months)

I want to push back on treating this as "a potential niche." **It should be the entire MVP focus**, for four reasons:

1. **Acute, recurring, high-stakes pain.** Missing a scholarship deadline has a concrete cost. The user *knows* they have this problem.
2. **Documents are structurally homogeneous.** University circulars, admission notices, and scholarship announcements share a layout grammar. One well-tuned schema covers most of the corpus — extraction quality will be visibly better than a generic tool's.
3. **A real technical moat.** Bangla OCR, Bangla date formats (`১৫ সেপ্টেম্বর ২০২৬`), Bangla–English code-switching, and government-PDF layout chaos are genuinely hard. General tools do this badly. Solving it is defensible.
4. **Distribution is free.** Facebook groups, university Discords, and Telegram channels for admission news are large and reachable.

Horizontal document AI ("upload any document!") has no wedge, no differentiated quality, and no distribution. Do not start there.

### Persona table

| Persona | Volume | Core job | Willingness to pay | MVP? |
|---|---|---|---|---|
| **Rafi — undergrad applicant** | 5–30 docs/mo | "Don't let me miss a deadline or show up without the right documents" | Low ($0–3/mo); converts via urgency | ✅ Primary |
| **Nusrat — university admin/coordinator** | 50–300 docs/mo | "Turn every circular into a checklist my 400 students can see" | Medium ($20–60/mo, dept budget) | ⚠️ Phase 2 (needs sharing) |
| **Tanvir — SME ops lead** | 30–150 docs/mo | "What are we obligated to do under these contracts and by when?" | High ($50–200/mo) | ❌ Phase 3 (needs contract schemas) |
| **Legal/compliance team** | 500+ docs/mo | Audit trail, retention, SSO, DPA | Very high | ❌ Phase 4 (needs SOC2-shaped controls) |

**Design implication:** build for Rafi, but never build anything that *blocks* Nusrat. Concretely: workspaces and memberships exist in the schema from day one even though the MVP UI only ever shows one workspace per user. Adding the second user to a workspace should be a UI change, not a migration.

---

## 4. Core User Journeys

### J1 — First value in under 90 seconds (the activation journey)

This is the only journey that matters for retention. Optimize it ruthlessly.

```
0:00  Land on marketing page → "Try it with a sample circular" (no signup)
0:05  Sample doc opens in the viewer, analysis pre-computed (fixture, zero latency)
0:20  User clicks the "September 15, 2026" deadline chip
0:21  PDF pane scrolls to page 3, the sentence pulses yellow      ← the "oh." moment
0:35  User clicks "Create 4 tasks from this document"
0:40  Task list appears with due dates back-derived from the deadline
0:50  "Save these to your workspace" → signup wall (Google OAuth, 1 click)
1:20  User uploads their own PDF
1:25  202 Accepted → live stage progress (parsing → OCR → extracting → indexing)
1:50  Analysis ready → notification → user is now activated
```

**Non-obvious requirements this journey creates:**
- A read-only **demo workspace** with pre-processed fixtures, reachable without auth. Also doubles as the hackathon demo and the E2E test fixture.
- Signup must be *after* value, not before it.
- Processing progress must be per-stage and honest, not a fake spinner. Users tolerate 90 seconds if they can see movement.

### J2 — Upload → tracked obligations (the core loop)

```
Upload (drag-drop, ≤25 MB, PDF/DOCX/TXT/PNG/JPG)
  → client-side MIME + size precheck, SHA-256 computed in a Web Worker
  → POST /v1/documents (multipart) → 202 { document_id, status: "queued" }
  → dedup: identical SHA-256 in this workspace returns the existing document
  → viewer opens immediately in "processing" state, polling stage events
  → stages stream in: validating → parsing → ocr → extracting → indexing → completed
  → analysis panel populates progressively (summary first, then facts, then tasks)
  → user reviews facts: ✓ confirm, ✎ edit, ✗ reject   ← this feedback is eval gold
  → "Generate tasks" → deterministic rules + LLM ordering → task list
  → tasks land on the calendar; reminders scheduled at T-14/7/3/1/0
```

**The review step is strategically important.** Every confirm/edit/reject is a free human label. Store it in `fact_reviews`. Within a month you have a real eval set contributed by users, and a measurable "extraction acceptance rate" metric — which is worth more than any benchmark.

### J3 — Grounded question answering

```
User asks: "Do I need to submit the NID before or after the interview?"
  → scope resolution: this document | this workspace
  → if single doc AND doc < 30k tokens → full document in context (no retrieval)
  → else → hybrid retrieval → RRF fusion → rerank → top-k context
  → answer streamed via SSE with inline citation markers [1] [2]
  → each marker is a clickable pill → scrolls the PDF pane + highlights
  → if no sufficiently grounded chunk: "I couldn't find this in the document"
     ← refusal is a feature; measure the refusal rate on the eval set
```

### J4 — Deadline surfaces

```
Dashboard "Next 14 days" strip → task detail → source fact → source page
Calendar view (month/agenda) → filter by workspace/document/status
Email digest (weekly Monday 8am local) + per-deadline reminders
GET /v1/deadlines.ics → subscribe in Google Calendar / Apple Calendar   ← Phase 2, high leverage
```

The `.ics` feed is disproportionately valuable: it puts DocFlow inside a surface the user already checks daily, at almost zero engineering cost.

### J5 — Team collaboration (Phase 2)

```
Owner invites member by email → role: owner | admin | member | viewer
Shared documents, shared tasks, assignment, @mentions in comments
Audit log: who viewed / downloaded / exported which document, when
```

---

## 5. Competitive Differentiation

| | ChatPDF / AskYourPDF | Notion AI / Mem | Docsumo / Rossum | **DocFlow AI** |
|---|---|---|---|---|
| Output | Chat answer | Summary block | Table rows (invoices) | **Typed obligation graph** |
| Provenance | Page number, sometimes | None | Field-level bbox | **Verified quote + bbox, per fact** |
| Verification | None | None | Confidence score | **Computed + calibrated confidence, review loop** |
| Action layer | ✗ | Manual | ✗ (export to ERP) | **Tasks, reminders, calendar, ICS** |
| Bangla | Poor | Poor | ✗ | **Tiered OCR + Bangla date normalization** |
| Pricing posture | $5–20/mo | Bundled | $500+/mo enterprise | Freemium → $5–15 → team |

### The three defensible moats

1. **Verification infrastructure.** The span-alignment + calibration + review-loop machinery is weeks of work and is invisible from the outside. Competitors that bolt on "citations" by asking the LLM for a page number cannot match it, and their users find out the hard way.
2. **Localized correctness.** Bangla OCR quality gates, Bangla numeral/month parsing, code-switched documents, and Bangladeshi institutional layout patterns. This is unglamorous and hard to copy quickly.
3. **The action graph as retention.** Once a user's deadlines live in DocFlow and feed their calendar, switching cost is real. A chat product has zero switching cost.

### Where a competitor beats us (be honest)

- **Raw chat quality on long English documents.** A well-funded generic tool with a bigger model budget will answer better. We do not compete there; we compete on structured output correctness.
- **Enterprise procurement.** No SOC 2, no SSO, no DPA for 18+ months. Do not chase enterprise deals early — they will consume the roadmap.
- **Breadth of file formats.** No PPTX, XLSX, EML, HTML in MVP. Deliberate.

---

## 6. MVP Definition

### The scoping rule

> If a feature does not appear in journey **J1** or **J2**, it is not in the MVP.

Estimated at **5–7 weeks of solo part-time work (~15 h/week)**, or ~3 weeks full-time. If it looks bigger than that when you start building, cut Bangla OCR escalation and workspace-scope RAG first.

### ✅ In the MVP

**Auth & tenancy**
- Email+password (argon2id) + Google OAuth; email verification; password reset
- Access JWT (15 min, in memory) + rotating refresh token (httpOnly cookie, 30 d)
- One auto-created workspace per user; `workspace_id` on every row; Postgres RLS enabled
- Roles defined (`owner`/`admin`/`member`/`viewer`) and enforced server-side; invite UI deferred

**Ingestion & processing**
- Upload PDF / PNG / JPG / TXT / DOCX, ≤25 MB, ≤150 pages
- Magic-byte MIME validation, SHA-256 dedup, quarantine→promote bucket flow
- **Normalization to canonical PDF** (DOCX/TXT via LibreOffice headless; images wrapped into a 1-page PDF)
- Per-page: text extraction (PyMuPDF) → word-level text map → 150 DPI WebP render
- Tiered OCR: native text layer → Tesseract `eng+ben` (word boxes) → quality gate → cloud/VLM fallback
- Stage state machine persisted in Postgres; ARQ workers; retries with backoff; per-stage idempotency

**AI**
- Classification (Haiku): `document_type`, `language`, `is_scanned`, `complexity`
- Structured extraction (Sonnet 5) over page windows → merge, with `strict: true` tool schemas
- Fact kinds: `deadline`, `requirement`, `action_item`, `entity`, `contact`, `link`, `risk`
- Deterministic date normalization (raw string → `dateparser` + Bangla numerals → `due_at`)
- **Span alignment + citation verification** (rapidfuzz → char offsets → word boxes → rects)
- Computed confidence, bucketed to high/medium/low
- Chunking + embeddings (`text-embedding-3-small`) → pgvector HNSW
- Grounded Q&A: full-doc context under 30k tokens, hybrid retrieval above it; SSE streaming with citations

**Action layer**
- Tasks generated from facts (rule-based + LLM ordering), linked to `source_fact_id`
- Manual task CRUD, status, due date, "Next 14 days" dashboard strip
- Basic month/agenda calendar

**Frontend**
- Landing page, auth pages, dashboard, upload, processing view, **split document viewer with working highlight navigation**, tasks, calendar, settings
- Demo workspace (no signup), command palette (⌘K)

**Ops**
- Docker Compose (postgres+pgvector, redis, minio, api, worker, web)
- Alembic migrations, seed script, GitHub Actions (lint/type/test/build/migration-drift/secret-scan)
- Structured logging + request IDs, Sentry, `usage_records` for every AI call
- Eval harness + **40–60 hand-labeled documents**, results committed to `docs/EVALUATION.md`

### ❌ Explicitly NOT in the MVP

| Not building | Why | When |
|---|---|---|
| Team invites / member management UI | Schema supports it; UI is a week of work with zero MVP-user value | Phase 2 |
| Billing, Stripe, subscriptions, plans | No paying users yet. Enforce free-tier *quotas* instead — 3 lines of code. | Phase 4 |
| Reranker model | Corpora are tiny (one document). Measure the retrieval gap **first**, then add. | Phase 3, gated on eval |
| Reranking, MMR, query decomposition, HyDE | Same reason. Every one of these is a solution to a problem you have not measured yet. | Phase 3, gated on eval |
| WebSockets / realtime collaboration | Polling with backoff is 15 lines and sufficient. SSE next, not WS. | Phase 2 (SSE) |
| Presigned direct-to-storage upload | 25 MB through FastAPI is fine. Adds 2 endpoints + a state machine for zero MVP value. | Phase 2, when limit → 100 MB |
| Full OTel + Prometheus + Grafana in prod | Sentry covers 90% of it for free. Ship the LGTM Compose profile for the *demo*, not for prod. | Phase 2 |
| PPTX / XLSX / EML / HTML ingestion | Each is a new parser + a new viewer path | Phase 3 |
| Multi-document / cross-document reasoning | Hard, and not in J1/J2 | Phase 3 |
| Fine-tuning, self-hosted LLMs | You have no data and no GPU budget | Maybe never |
| Mobile app | PWA-responsive web covers it | Phase 4 |
| Admin panel | `psql` and a Metabase container cover it | Phase 3 |
| Slack/Notion/Drive integrations | Every one is an OAuth flow + a sync engine | Phase 4 |
| Soft-delete on every table | Only `documents`, `tasks`, `workspaces`. Everywhere else it is dead weight and a query-correctness hazard. | Never |

### MVP acceptance criteria (binary, testable)

1. A new user signs up and gets a working analysis of their own PDF in **< 3 minutes** end to end.
2. Clicking any high-confidence deadline navigates the viewer to the correct page and highlights text overlapping the true source span — verified in a Playwright E2E test.
3. On the eval set: deadline-extraction **F1 ≥ 0.80** and **citation verification rate ≥ 0.90** for facts shown as confirmed. *(Numbers to be measured, not assumed — if the real number is 0.72, publish 0.72.)*
4. Cross-tenant access is impossible: an automated test attempts read+write on every table as a foreign tenant and all attempts fail.
5. A scanned Bangla circular produces at least the correct document type, one correct deadline, and page-level citations.
6. `docker compose up` on a clean machine yields a fully working seeded app in under 10 minutes.
7. p95 API latency (excluding LLM streaming) **< 400 ms**.
8. Zero secrets in the repo (gitleaks clean); all CI gates green on `main`.

---

## 7. Full Feature Roadmap

### Phase 1 — MVP (weeks 1–7)
As defined above.

### Phase 2 — Production hardening & collaboration (weeks 8–13)
- SSE progress + streaming chat events (replaces polling)
- Presigned direct upload; raise limit to 100 MB / 500 pages
- Team invites, roles enforced in UI, per-document sharing
- Audit log (append-only) + audit viewer
- Email notifications (Resend), digest scheduling, `deadlines.ics` feed
- Rate limiting per user/workspace/endpoint-class; per-workspace cost budgets with hard stop
- OpenTelemetry traces → Grafana Cloud free tier; core dashboards + alerts
- Backups: Neon PITR + weekly `pg_dump` to R2; documented restore drill
- Fact review loop shipped; acceptance-rate metric on the dashboard
- ClamAV scanning in the quarantine stage
- Playwright E2E on the critical paths, running in CI

### Phase 3 — Advanced AI (weeks 14–24)
- Contextual retrieval (LLM-generated chunk headers), **A/B'd against the eval set**
- Reranker (Cohere Rerank vs. self-hosted BGE-reranker-v2-m3), **gated on measured Recall@k gap**
- `bge-m3` self-hosted embeddings benchmarked against `text-embedding-3-small` on the Bangla set
- Cross-document Q&A and workspace-wide semantic search
- New verticals via the schema registry: contracts, invoices, HR letters
- Table extraction (structured tables → typed rows)
- Document comparison / diff (v1 vs v2 of a circular — genuinely useful, nobody does it)
- Auto-labeling tool at `/admin/eval` reusing the viewer
- Batch API path for documents > 50 pages (50% cost reduction)
- Nightly eval regression job posting results to a dashboard

### Phase 4 — SaaS business (months 6–12)
- Stripe billing, plans, metered overage on the `usage_records` table
- Public API + API keys + per-key scopes and rate limits
- Webhooks (`document.completed`, `deadline.approaching`)
- Integrations: Google Drive, Gmail ingestion, Slack notifications, Notion export
- Admin console, impersonation with audit, support tooling
- SSO (WorkOS), SCIM, DPA, data-residency options
- Mobile PWA with push notifications

### Phase 5 — Platform (year 2+)
- Custom extraction schema builder (users define their own document types in the UI)
- Workflow automation ("when a document of type X is ingested, do Y")
- Organization-level obligation graph and reporting
- On-prem / VPC deployment for regulated buyers

---

*Next: [10-architecture.md](10-architecture.md) — system architecture, pipeline, jobs, multi-tenancy, deployment.*
