# DocFlow AI — Part 1: System Architecture

> Sections 8 (system architecture), 11 (document pipeline), 15 (background jobs), 17 (multi-tenancy), 26 (deployment).

---

## 8. System Architecture

### 8.1 The shape of the system

```
                         ┌──────────────────────────────────────┐
                         │  Browser (Next.js 15 App Router)     │
                         │  RSC shell · TanStack Query · pdf.js │
                         └───────────────┬──────────────────────┘
                                         │ HTTPS · Bearer JWT (memory)
                                         │ Refresh cookie (httpOnly, /auth/refresh only)
                         ┌───────────────▼──────────────────────┐
                         │  Next.js Route Handlers (thin BFF)   │
                         │  cookie↔header bridge, SSR fetches   │
                         └───────────────┬──────────────────────┘
                                         │
        ┌────────────────────────────────▼─────────────────────────────────┐
        │  FastAPI  (apps/api · uvicorn · 2–4 workers)                     │
        │  ┌────────────────────────────────────────────────────────────┐  │
        │  │ Middleware: request-id → auth → tenant-ctx (SET LOCAL) →   │  │
        │  │             rate-limit → problem+json errors → OTel span   │  │
        │  └────────────────────────────────────────────────────────────┘  │
        │  routers/  ·  services/  ·  ai/  ·  models/                      │
        └──┬───────────────┬──────────────┬────────────────┬───────────────┘
           │               │              │                │
   ┌───────▼──────┐ ┌──────▼──────┐ ┌─────▼──────┐ ┌───────▼─────────┐
   │ PostgreSQL16 │ │   Redis 7   │ │ Object     │ │ LLM providers   │
   │ + pgvector   │ │ queue·cache │ │ store (R2) │ │ Anthropic/OpenAI│
   │ + RLS + FTS  │ │ ratelimit   │ │ MinIO local│ │ /Gemini/local   │
   └───────▲──────┘ └──────▲──────┘ └─────▲──────┘ └───────▲─────────┘
           │               │              │                │
        ┌──┴───────────────┴──────────────┴────────────────┴───┐
        │  ARQ Worker (same image, different entrypoint)        │
        │  pipeline stages · ProcessPoolExecutor for CPU work   │
        │  cron: reminders, digests, retention purge, eval      │
        └───────────────────────────────────────────────────────┘
```

### 8.2 Why this shape — and what I rejected

**Decision: FastAPI + a separate worker process, sharing one Python package and one Docker image.**

*Why:* Document processing takes 20–120 s. It cannot live in a request. But splitting `api` and `worker` into separate *packages* means duplicating SQLAlchemy models, settings, the LLM client, and the storage adapter — which is where drift bugs come from. Same package, two entrypoints (`docflow.main:app` and `docflow.worker.main`), two containers from one image, independent scaling.

*Rejected:*
- **Everything in Next.js API routes.** Python owns the document/ML ecosystem (PyMuPDF, Tesseract, rapidfuzz, dateparser). Reimplementing span alignment in TS is masochism. Also: serverless functions and 90-second CPU-bound OCR are a bad marriage.
- **Serverless workers (Lambda/Cloud Run jobs).** Cold starts on a container with Tesseract + LibreOffice are 5–15 s, and you pay for the whole container during LLM wait time. A long-lived worker with async concurrency is cheaper and simpler here.
- **A single monolith serving both HTTP and jobs in-process.** Tempting for the MVP, but one OOM from a 500-page PDF takes down your API. Hard no.
- **Microservices (separate ingestion / extraction / RAG services).** Zero benefit at this scale, enormous local-dev and tracing cost. Revisit at >50 engineers, i.e. never for this project.

**When NOT to use this architecture:** if your documents were all <2 pages and processed in <3 s, skip the worker entirely and process inline. Complexity here is bought by the 20–120 s processing time.

### 8.3 Component responsibilities

| Component | Owns | Explicitly does not own |
|---|---|---|
| `web` (Next.js) | Rendering, client state, PDF viewer, optimistic UI | Business rules, authz decisions, LLM calls |
| Next route handlers | Cookie↔header bridging, SSR data fetch, CSP nonce | Any logic (proxy only — keep them dumb) |
| `api` (FastAPI) | AuthN/Z, validation, tenant context, CRUD, enqueue, SSE, RAG query path | CPU-heavy parsing, OCR, long LLM chains |
| `worker` (ARQ) | Pipeline stages, embeddings, extraction, cron jobs | Serving HTTP |
| Postgres | System of record **including pipeline state**, vectors, FTS | Blobs |
| Redis | Job queue, rate-limit counters, response/embedding cache, SSE pub-sub | Anything durable |
| Object store | Original files, canonical PDFs, page renders, exports | Metadata |

### 8.4 The LLM provider abstraction

**Abstract at the *tier*, not the model.**

```
ModelTier = FAST | BALANCED | DEEP | EMBED
```

Config maps tier → `(provider, model, effort, max_tokens)` per environment. Call sites ask for `ModelTier.FAST`; they never name a model. This makes model routing, provider migration, cost experiments, and eval sweeps configuration changes rather than code changes.

```
LLMResponse:
  content: str | None
  parsed:  BaseModel | None        # validated structured output
  usage:   input, output, cache_read, cache_write tokens
  model, provider, latency_ms, cost_usd, finish_reason, provider_request_id
```

Every call goes through one `call_llm()` chokepoint that: enforces a timeout, retries 429/5xx with jittered backoff, checks the workspace budget **before** spending, writes a `usage_records` row **after**, emits an OTel span with GenAI semantic conventions, and validates structured output against the Pydantic model (with exactly one repair round-trip on validation failure).

*Why not LangChain:* it abstracts away exactly the things this product needs to control — token accounting, cache breakpoints, retry semantics, cost attribution — and its abstractions churn between minor versions. Three thin adapters (Anthropic, OpenAI, Gemini) total ~300 lines and you can read all of them.
*Why not LiteLLM:* closer call. It saves the adapter code, but it normalizes away provider-specific features you actually want (Anthropic prompt-cache breakpoints, `effort`, `strict` tool schemas) and puts a large dependency in your hot path. If you were supporting 15 providers, use LiteLLM. For 3, write the adapters.
*Where a framework does earn its place:* nothing in the MVP. Not `instructor` (the retry-on-validation-error loop is ~80 lines and you want it inside your chokepoint anyway). Consider `pgvector`'s SQLAlchemy integration and `rapidfuzz` — libraries, not frameworks.

**Do not abstract away provider superpowers.** Expose them as optional kwargs that no-op on other providers: `cache_breakpoints=[...]`, `effort="low"`, `strict_schema=True`, `batch=True`.

### 8.5 Model routing policy (MVP)

| Task | Tier | Model | Rationale |
|---|---|---|---|
| Classification, language detect | FAST | `claude-haiku-4-5` | Trivial task, 5× cheaper |
| Fact extraction | BALANCED | `claude-sonnet-5` | Quality matters most here; this is the product |
| Fact merge / dedup across windows | FAST | `claude-haiku-4-5` | Mechanical |
| Contextual chunk headers (Ph.3) | FAST | `claude-haiku-4-5` + caching | 150 calls/doc — cost dominates |
| Chat: simple lookup | FAST | `claude-haiku-4-5` | Router classifies query complexity first |
| Chat: reasoning/multi-hop | BALANCED | `claude-sonnet-5` | |
| Escalation on low confidence | DEEP | `claude-opus-5`, `effort: high` | Only when composite confidence < 0.5 **and** the doc is user-flagged |
| Eval judge | DEEP | `claude-opus-5` | Must differ from the generator model |
| Embeddings | EMBED | `text-embedding-3-small` (1536d) | Cheap, good enough; benchmark `bge-m3` in Ph.3 |

> **Never route the judge model to the same model that generated the output.** Self-evaluation inflates scores.

---

## 11. Document Processing Pipeline

### 11.1 The canonical-representation decision

**Every uploaded file is normalized to three artifacts before any AI touches it:**

1. `canonical.pdf` — the PDF form of the document
2. `page_{n}.webp` — a 150 DPI render of each page (viewer fallback + OCR input + VLM input)
3. **The text map** — for each page, the full text string plus a parallel array of `(char_start, char_end, x0, y0, x1, y1)` word entries in *rotation-normalized display space*

| Input | → canonical.pdf via |
|---|---|
| PDF | passthrough (repair with `pikepdf` if malformed) |
| DOCX | LibreOffice headless `--convert-to pdf` |
| TXT / MD | render to PDF with a Bangla-capable font (Noto Sans Bengali) |
| PNG / JPG / WEBP | wrap into a 1-page PDF at native resolution |

**Why this is the highest-leverage decision in the pipeline:** without it you need a DOCX viewer, an image viewer, a text viewer, and three separate highlight-coordinate models. With it, the frontend has exactly one viewer and one highlight implementation, and citation quality is uniform across formats.

*Cost:* LibreOffice is a ~450 MB layer in the worker image and adds 2–5 s per DOCX. *Trade-off accepted.* DOCX→PDF fidelity is imperfect for complex layouts, but circulars and notices are simple. *When NOT to do this:* if DOCX were your dominant format and users needed reflowable editing, keep the native DOCX path and cite by paragraph index instead.

### 11.2 The stage machine

```
uploaded → validating → normalizing → parsing → [ocr] → segmenting
         → classifying → extracting → aligning → chunking → embedding
         → indexing → completed
                 └──────────────→ failed | quarantined | partial
```

`partial` matters: if extraction succeeds but embedding fails, the user should still see their deadlines. Never let a late stage invalidate an early one.

| Stage | Does | Idempotency key | Retries | Typical |
|---|---|---|---|---|
| `validating` | magic bytes, size, page count, SHA-256, ClamAV (Ph.2), quarantine→promote | `doc_id` | 2 | <1 s |
| `normalizing` | → canonical.pdf, page renders | `doc_id + file_sha` | 3 | 1–8 s |
| `parsing` | PyMuPDF text + word boxes per page → text map | `doc_id + canonical_sha` | 3 | 1–5 s |
| `ocr` | per-page, only where the text layer is thin | `doc_id + page_no` | 3/page | 1–3 s/page |
| `segmenting` | headings from font-size/weight, section paths, reading order | `doc_id` | 2 | <1 s |
| `classifying` | type, language, is_scanned, complexity | `doc_id + prompt_v` | 3 | 2–5 s |
| `extracting` | per page-window structured extraction | `doc_id + window + prompt_v + model` | 3/window | 5–20 s/window |
| `aligning` | quote → char offsets → word boxes → rects; confidence | `doc_id + fact_id` | 2 | <1 s |
| `chunking` | structure-aware windows with page/char/section metadata | `doc_id + chunk_cfg_v` | 2 | <1 s |
| `embedding` | batch embed, cache by content hash | `chunk_id + model` | 3 | 1–5 s |
| `indexing` | write vectors + `tsvector`, refresh stats | `doc_id` | 3 | <2 s |

### 11.3 Idempotency and resumability without Temporal

**Pipeline state lives in Postgres, not in the queue.**

```
document_stages(document_id, stage, status, attempt, started_at,
                finished_at, error_code, error_detail, output_ref,
                idempotency_key)
UNIQUE (document_id, stage)
```

The worker's job body is: *"advance document D to completion."* On each invocation it reads `document_stages`, finds the first non-`completed` stage, and runs it. Every stage begins with `if stage.status == 'completed' and stage.idempotency_key == computed_key: skip`.

**What this buys:**
- A crashed worker mid-`extracting` resumes at `extracting` on the next attempt — pages already parsed and OCR'd are not redone. On a 100-page scanned document this is the difference between $0.60 and $0.00 of wasted spend.
- Changing the extraction prompt changes `prompt_v`, which changes the idempotency key, which re-runs *only* that stage. Backfills become free.
- The queue library becomes a delivery mechanism. Swapping ARQ→Celery is ~200 lines and no semantic change.

*This is durable execution's 20% that delivers 80% of the value.* **When NOT to do this:** if your workflow had dozens of stages with complex fan-out/fan-in and human-in-the-loop waits, buy Temporal. For a linear 11-stage pipeline, this is the right amount of machinery.

### 11.4 OCR: the tiered strategy

> **Measured correction (2026-08-21).** For Bengali the native text layer can be
> *worse* than OCR: ligature shaping without a ToUnicode mapping yields
> plausible-looking text that scores 63–70 on alignment (every citation
> UNVERIFIED) while OCR of the same page scores 100. Tier 1 therefore also
> checks for **script corruption**, not just absence of text.
> See [EVALUATION.md](EVALUATION.md) §7.

```
For each page:
  1. Native text layer (PyMuPDF).
     Accept if: chars ≥ 120  AND  word-box coverage ≥ 0.5% of page area
                AND garbage-char ratio < 0.15
                AND script-corruption ratio < 0.05   ← MEASURED, see §7
     (0.5%, not 40% as an earlier draft said — MEASURED word-box coverage on A4:
      blank 0.000 | one line 0.012 | four lines 0.025 | dense full page 0.309.
      A 40% gate would route every real page to OCR.)
  2. Else: render 300 DPI → deskew/denoise (OpenCV) → Tesseract `eng+ben`, TSV output
     → word-level boxes and per-word confidence.
     Accept if: mean word confidence ≥ 70 AND dictionary-hit ratio ≥ 0.35
  3. Else: escalate ONE of
        (a) Cloud OCR (Google Document AI / Azure Document Intelligence) — keeps word boxes
        (b) VLM pass (page image → Claude/Gemini) — better on chaotic layouts, NO word boxes
  4. If (b) was used: mark page `citation_granularity = 'page'`.
     Facts from that page get page-level citations and no highlight rectangle.
     The UI says "Page 3" without a highlight — honestly degraded, never faked.
```

**Engine choice, with reasoning:**

| Engine | Bangla | Word boxes | Cost | Verdict |
|---|---|---|---|---|
| **Tesseract 5 + `ben`** | **Measured 100/line on rendered Bengali** | ✅ TSV | Free, CPU | ✅ **Default tier 2**, and tier 1 for Bengali pages with a corrupt text layer. |
| PaddleOCR | Better on many scripts | ✅ polygons | Free, wants GPU | ⚠️ Heavy install, painful on ARM Mac. Benchmark in Ph.3. |
| Surya | Strong multilingual | ✅ | Free, needs GPU | ⚠️ Best open option *if* you get a GPU. Ph.3. |
| Google Document AI / Azure DI | Strong | ✅ | ~$1.50/1k pages | ✅ **Tier 3a.** Pay only on the ~10% that fail the gate. |
| VLM (Claude/Gemini vision) | Strong | ❌ | ~$0.01/page | ✅ **Tier 3b** for layout chaos. Accept page-level citations. |

**The critical trade-off nobody mentions:** VLM-based OCR reads messy documents better than any traditional engine — *and destroys your highlight feature*, because it returns text without coordinates. That is why it is a fallback, not the default. If you make VLM OCR the primary path, you have quietly traded away the product's differentiator for parsing convenience.

### 11.5 Span alignment — how citations actually work

This is the core algorithm. Everything else is plumbing.

```
INPUT   fact.source_quote  (LLM-produced, may be lightly paraphrased or truncated)
        fact.source_page   (LLM-produced, may be wrong)

1. Normalize both quote and page text: NFKC, collapse whitespace,
   fold Bangla numerals to ASCII, strip zero-width joiners.
   Keep an index map back to original offsets.

2. Search the claimed page with rapidfuzz.fuzz.partial_ratio_alignment().
   If score < 88, search ±2 pages, then the whole document (bounded).

3. Best match → (char_start, char_end) in the normalized page text
   → map back through the index to raw offsets
   → binary-search the word array for words overlapping [start, end)
   → group words by line → union boxes per line → 1..N rectangles

4. Store rects normalized to [0,1] against the page's DISPLAY box
   (post-/Rotate), so the frontend never redoes rotation math.

5. Record citation_status:
     verified   score ≥ 95
     partial    88 ≤ score < 95   → shown, badged "approximate"
     unverified score < 88        → fact demoted to low confidence,
                                     hidden behind "show unverified"
```

**Why fuzzy, not exact:** LLMs normalize whitespace, expand abbreviations, drop diacritics, and truncate mid-word. Exact matching fails on ~30% of genuinely correct quotes. Fuzzy matching with a threshold plus an honest status label is the correct engineering answer.

**Why this is also your anti-hallucination gate:** a fabricated fact cannot produce a quote that fuzzy-matches the source at ≥ 95. Citation verification rate therefore doubles as a *fully automatic, judge-free hallucination metric*. This is the single most valuable metric in the eval suite and it costs nothing to compute.

**Rotation gotcha (will bite you):** PyMuPDF's `page.rect` reflects `/Rotate`; `page.mediabox` does not. Word boxes from `get_text("words")` are in unrotated space. Apply `page.rotation_matrix` before normalizing, and store rects relative to `page.rect`. Write a unit test with a 90°-rotated fixture PDF — this bug is invisible until a user uploads a scanned landscape page.

### 11.6 Chunking

- Structure-aware: split at detected headings (font size ≥ 1.15× body median, or bold + short line), then pack to ~700 tokens with ~12% overlap.
- **Never silently cross a page boundary** — record `page_start` and `page_end`.
- Every chunk carries `section_path` (e.g. `"Eligibility > Required Documents"`), prepended to the embedded text. This is a free 80% substitute for LLM-generated contextual headers.
- Store `char_start`/`char_end` so chat citations resolve to rectangles through the exact same alignment path as extraction citations. **One citation implementation, two consumers.**

---

## 15. Background Job Architecture

### 15.1 ARQ vs Celery — the actual comparison

| | **ARQ** | **Celery** |
|---|---|---|
| Model | asyncio-native | sync-first (async support is partial and awkward) |
| Broker | Redis only | Redis, RabbitMQ, SQS |
| Setup | ~30 lines | config sprawl, easy to misconfigure |
| Result backend | Redis, simple | Redis/DB, many footguns |
| Canvas (chains/chords/groups) | ✗ | ✅ |
| Ecosystem / Flower / recruiter recognition | thin | huge |
| Maintenance velocity | slow | active |
| Fit with FastAPI's async DB session | native | needs a second sync stack |

**Recommendation: ARQ for MVP.**

*Why:* the pipeline's wall-clock time is dominated by **awaiting network I/O** — LLM calls, embedding calls, cloud OCR, S3. Async concurrency lets one worker process hold 20 documents in flight against ~2 CPUs. Celery's process-per-task model would need 20 processes for the same throughput. ARQ also shares the exact async SQLAlchemy session code with FastAPI — one DB stack, not two.

*The obvious objection:* OCR and PDF parsing are CPU-bound and will block the event loop. **Answer: they run in a `ProcessPoolExecutor` via `loop.run_in_executor()`.** This is ~10 lines and gives true parallelism for CPU work while keeping I/O async. It also isolates PDF parsing in a subprocess with a memory cap and a hard timeout — which is a *security* win (§16: malformed-PDF OOM/DoS).

*Celery's canvas is the one real loss* — and the Postgres stage machine (§11.3) replaces it with something better for this use case: inspectable in SQL, resumable, and not dependent on broker state.

**Switch to Celery when:** you need multiple brokers, complex fan-out/fan-in with result aggregation, priority queues with strict SLAs, or a team already fluent in it. **Do not choose ARQ if** nobody on the team has read its (small) source — its docs are thin and you will read the source eventually.

**Third option considered and rejected for now:** a Postgres-backed queue via `SELECT … FOR UPDATE SKIP LOCKED` (e.g. `procrastinate`). Genuinely attractive — one fewer system, transactional enqueue with your writes. Rejected because Redis is already present for caching and rate limiting, so it saves nothing operationally. *Revisit if you ever drop Redis.*

### 15.2 Queues and concurrency

| Queue | Concurrency | Timeout | Purpose |
|---|---|---|---|
| `docflow:ingest` | 8 | 15 min | The document pipeline |
| `docflow:ai` | 12 | 5 min | Standalone LLM work (re-extraction, contextual headers) |
| `docflow:notify` | 4 | 60 s | Email, digests, webhooks |
| `docflow:cron` | 2 | 10 min | Reminders, retention purge, nightly eval |

Separate queues so a burst of uploads never delays reminder emails, and a stuck LLM provider never stalls ingestion of already-parsed documents.

### 15.3 Retry, backoff, failure

- **Retryable:** 429, 5xx, connection errors, timeouts, transient storage errors. Exponential backoff with full jitter, `base=2s`, `cap=120s`, max 3 attempts per stage.
- **Non-retryable (fail fast):** validation failures, unsupported MIME, encrypted PDF, page-count exceeded, schema validation failing twice, budget exceeded. These write a typed `error_code` the UI can explain in plain language.
- **Poison-message protection:** ARQ's `max_tries` plus a `document_stages.attempt` counter. On final failure → `status = failed`, `error_code` set, Sentry event with the `document_id` and `request_id`, and the user sees an actionable message ("This PDF is password-protected. Remove the password and re-upload.") — never a stack trace, never "Something went wrong."
- **Dead-letter:** failed documents stay queryable. `POST /v1/documents/{id}/reprocess` re-enqueues from the first failed stage; completed stages are skipped by idempotency key.

### 15.4 Cron jobs

| Job | Cadence | Notes |
|---|---|---|
| `schedule_reminders` | every 15 min | Scan tasks with `due_at` in the T-14/7/3/1/0 windows. `UNIQUE (task_id, offset_days)` in `notifications` makes it exactly-once regardless of overlap. |
| `weekly_digest` | Mon 08:00 per-user TZ | Bucket users by timezone; run hourly and pick the bucket whose local time is 08:00. |
| `retention_purge` | daily 03:00 | Hard-delete storage objects for documents soft-deleted > 30 days ago. |
| `usage_rollup` | hourly | Aggregate `usage_records` → `usage_daily` for fast dashboards. |
| `eval_regression` | nightly | Full eval suite; write `eval_runs`; alert on metric regression > 3 points. |
| `orphan_sweep` | daily | Storage objects with no DB row (failed uploads) → delete after 24 h. |

---

## 17. Multi-Tenancy Strategy

### 17.1 The tenant boundary

**`workspace` is THE tenant.** Every tenant-scoped table carries `workspace_id UUID NOT NULL`.

**I am cutting `organizations` from the MVP.** Having both `organizations` and `workspaces` on day one adds a join, a second permission surface, and an entity users cannot explain — with zero MVP value. When billing arrives (Phase 4), add `organizations` *above* workspaces as the billing entity; that migration is additive (`ALTER TABLE workspaces ADD COLUMN organization_id`) and backfillable. Adding it now is speculative generality.

### 17.2 Isolation model: shared schema + `workspace_id` + Row-Level Security

**Chosen: shared database, shared schema, `workspace_id` column, application-level filtering, *with Postgres RLS as an enforced backstop*.**

Every request opens a transaction and sets:
```sql
SET LOCAL app.workspace_id = '<uuid>';
SET LOCAL app.user_id      = '<uuid>';
```
and every tenant table has:
```sql
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON documents
  USING (workspace_id = current_setting('app.workspace_id')::uuid)
  WITH CHECK (workspace_id = current_setting('app.workspace_id')::uuid);
```

**Why both layers.** App-level filtering alone fails the day someone writes a new endpoint and forgets one `WHERE`. That single omission is the most common cause of real-world SaaS tenant breaches. RLS converts a catastrophic data leak into an empty result set. The app filter stays because it keeps queries fast and index-friendly and makes intent explicit in code.

**Cost and gotchas (real):**
- `SET LOCAL` must be inside an explicit transaction. With PgBouncer in *transaction* pooling mode this is safe; in *session* mode it leaks across requests. **Pin PgBouncer to transaction mode and add a startup assertion.**
- The migration role must `BYPASSRLS`; the application role must **not**, and must not be the table owner unless `FORCE ROW LEVEL SECURITY` is set (it is, above).
- Background jobs have no HTTP request. The worker sets the same GUCs from the job payload's `workspace_id` at the top of each stage. Cross-tenant jobs (retention purge) use a separate elevated role and are individually reviewed.
- Cross-workspace admin queries need the elevated role — keep those in exactly one module.

**Alternatives, and when they'd be right:**

| Model | Isolation | Ops cost | Use when |
|---|---|---|---|
| Shared schema + `workspace_id` only | Weak (one bug away) | Lowest | Internal tools, single-tenant |
| **Shared + RLS (chosen)** | Strong | Low | Multi-tenant B2C/SMB SaaS ✅ |
| Schema-per-tenant | Strong | High (migrations × N, connection churn, catalog bloat at ~1000 schemas) | Tens of large enterprise tenants |
| Database-per-tenant | Strongest | Highest | Regulated/on-prem/data-residency |

Schema- and DB-per-tenant break pgvector badly: you'd maintain N HNSW indexes and lose cross-tenant index efficiency. Not worth it here.

### 17.3 Tenant isolation testing (do this — it is a portfolio asset)

A parameterized pytest suite that enumerates every tenant table from SQLAlchemy metadata and, for each, asserts as tenant B: `SELECT` returns zero rows of A's data, `UPDATE`/`DELETE` affect zero rows, and `INSERT` with A's `workspace_id` raises. **New tables are covered automatically** — a developer adding a table without RLS gets a red build. `tests/security/test_tenant_isolation.py` is a file recruiters will actually open.

### 17.4 Storage isolation

Object keys: `ws/{workspace_id}/doc/{document_id}/{artifact}`. Access is *never* by key guessing — the API authorizes, then mints a 5-minute presigned URL with `response-content-disposition: attachment` pinned for originals. No bucket-level public read, ever. Page renders get a slightly longer TTL (15 min) because the viewer fetches many of them.

### 17.5 Roles and permissions

| Capability | owner | admin | member | viewer |
|---|---|---|---|---|
| View documents / facts / tasks | ✅ | ✅ | ✅ | ✅ |
| Upload, delete own documents | ✅ | ✅ | ✅ | ✗ |
| Delete any document | ✅ | ✅ | ✗ | ✗ |
| Create/assign tasks | ✅ | ✅ | ✅ | ✗ |
| Chat / search | ✅ | ✅ | ✅ | ✅ |
| Invite / remove members | ✅ | ✅ | ✗ | ✗ |
| Change roles | ✅ | ✗ | ✗ | ✗ |
| Billing, delete workspace, transfer ownership | ✅ | ✗ | ✗ | ✗ |
| View audit log | ✅ | ✅ | ✗ | ✗ |

Implemented as a pure function `can(user_role, action, resource) -> bool` with an exhaustive table-driven test, injected as a FastAPI dependency (`require(Permission.DOCUMENT_DELETE)`). **Not** a general policy engine (OPA/Casbin) — 4 roles and ~15 actions do not justify one, and the pure-function version is trivially testable and readable.

---

## 26. Deployment Architecture

### 26.1 Recommended production topology

| Concern | Choice | Why | Cost |
|---|---|---|---|
| Web | **Vercel** | Next.js first-class, edge CDN, preview deploys per PR | $0 → $20/mo |
| API + Worker | **Fly.io** (2 apps, 1 image) | Runs Docker as-is, scale-to-zero for the worker, Singapore region for BD latency, cheap persistent processes | ~$10–25/mo |
| Postgres | **Neon** (pgvector, PITR) | Serverless branching gives every PR a real DB; pgvector preinstalled | $0 → $19/mo |
| Redis | **Upstash** | Per-request pricing, generous free tier, TLS | $0 → $10/mo |
| Object store | **Cloudflare R2** | **Zero egress fees** — decisive for a product that serves PDFs and page images repeatedly | ~$1–3/mo |
| Errors/traces | **Sentry** | Free tier covers a solo project's needs; Python + Next SDKs | $0 |
| Email | **Resend** | Simple API, good deliverability, free tier | $0 → $20/mo |

**Total: ~$0–30/mo pre-revenue, ~$60–90/mo for a small production load.**

### 26.2 Storage: why R2 over S3 and Supabase

| | R2 | AWS S3 | Supabase Storage |
|---|---|---|---|
| Storage | $0.015/GB-mo | $0.023/GB-mo | bundled |
| **Egress** | **$0** | **$0.09/GB** | bundled, then metered |
| S3 API compatible | ✅ | ✅ | ✅ (partial) |
| Ecosystem (Textract, Lambda triggers) | ✗ | ✅ | ✗ |

DocFlow serves the same PDF and its page images on **every viewer open**. A 5 MB PDF viewed 20×/month = 100 MB egress per document. At 10,000 documents that is 1 TB/month — **$90 on S3, $0 on R2**. This is not a marginal call.

*Choose S3 instead if* you plan to use Textract/Comprehend or need Lambda-on-upload triggers. *Choose Supabase Storage if* you're already using Supabase for auth+DB and want one vendor.

**Local dev uses MinIO** in Compose — S3-compatible, so `boto3` code is byte-identical across environments. One `S3_ENDPOINT_URL` env var switches them.

**R2 CORS must allow your web origin** or pdf.js cannot fetch the canonical PDF. This will be your first deploy bug; put it in the runbook.

### 26.3 Region strategy

Co-locate **API + worker + Postgres + Redis in one region**. A single request touches the DB 5–20 times; 80 ms of cross-region latency per hop is fatal, and it is the most common self-inflicted performance wound in this kind of stack.

For a Bangladesh-first audience: **Singapore (`sin`)** — ~50–70 ms RTT to Dhaka, and Neon/Fly/Upstash all offer it. Vercel's edge serves static assets globally regardless. R2 is global by design.

### 26.4 Alternative: single VPS

One Hetzner CX22 (~€5/mo) running Docker Compose with Postgres, Redis, MinIO, API, worker, and Caddy. **Cheapest possible, full control, and honestly defensible.** You take on backups, upgrades, uptime, and TLS renewal.

*Recommendation:* managed services for the portfolio/production build (demonstrates more, fails less), and keep the Compose file capable of full single-host deployment — that capability is itself worth showing, and it is your escape hatch if managed costs ever bite.

### 26.5 Scaling path (when the numbers say so)

| Trigger | Action |
|---|---|
| Ingest queue depth > 50 for 10 min | Scale worker replicas (stateless — just add machines) |
| p95 API latency > 500 ms | Add API replicas; check for N+1s before adding hardware |
| DB CPU > 70% | Add a read replica for search/list endpoints; keep writes on primary |
| pgvector recall degrading, > 5 M chunks | Raise `hnsw.ef_search`; then partition `document_chunks` by workspace; then consider a dedicated vector store |
| Embedding cost > $200/mo | Self-host `bge-m3` on a GPU box |
| Storage > 1 TB | R2 lifecycle rules: page renders → Infrequent Access after 90 days |

**Do not pre-build any of this.** Each row is a response to a metric you will have from §18.

---

*Next: [20-ai.md](20-ai.md) — AI architecture, RAG, evaluation, cost.*
