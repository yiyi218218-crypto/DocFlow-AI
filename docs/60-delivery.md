# DocFlow AI — Part 6: Delivery

> Sections 21 (repository), 22 (phases), 23 (milestones), 27 (hackathon), 28 (portfolio), 29 (startup roadmap), 30 (risks & tradeoffs).

---

## 21. Repository Structure

### 21.1 Monorepo, with one important simplification

**`worker` is not a separate app.** It is a second entrypoint in the same Python package. Separating them means duplicating models, settings, the DB session, the storage adapter, and the LLM client — which is where drift bugs breed. One package, two `CMD`s, one image, two containers.

```
docflow/
├── README.md                      ← the portfolio front door (see §28)
├── Makefile
├── docker-compose.yml
├── docker-compose.observability.yml   # profile: prometheus/grafana/tempo/loki
├── .env.example
├── turbo.json · pnpm-workspace.yaml
│
├── apps/
│   ├── web/                                   # Next.js 15
│   │   ├── app/                               # routes (see §14.2)
│   │   ├── components/
│   │   │   ├── ui/                            # shadcn primitives
│   │   │   ├── viewer/                        # PdfCanvas, HighlightLayer,
│   │   │   │                                  # PageVirtualizer, FactCard, SplitPane
│   │   │   ├── facts/ · tasks/ · chat/ · upload/ · layout/
│   │   ├── hooks/                             # useDocument, useAnalysis, useHighlight…
│   │   ├── lib/  api/ · query-keys.ts · stores/ · utils/
│   │   ├── e2e/                               # Playwright
│   │   └── package.json · next.config.ts
│   │
│   └── api/                                   # FastAPI + ARQ, one package
│       ├── src/docflow/
│       │   ├── main.py                        # FastAPI app factory
│       │   ├── core/
│       │   │   ├── config.py                  # Pydantic Settings, SecretStr
│       │   │   ├── database.py                # async engine, session, SET LOCAL tenant
│       │   │   ├── security.py                # JWT, argon2, refresh rotation
│       │   │   ├── permissions.py             # can(role, action, resource)
│       │   │   ├── logging.py · telemetry.py · errors.py   # problem+json
│       │   │   ├── rate_limit.py · idempotency.py · storage.py
│       │   ├── api/v1/
│       │   │   ├── router.py
│       │   │   ├── deps.py                    # current_user, workspace_ctx, require()
│       │   │   └── routes/  auth · workspaces · documents · facts · tasks
│       │   │                · deadlines · chat · search · usage · notifications
│       │   ├── models/                        # SQLAlchemy 2.0 (Mapped[...])
│       │   ├── schemas/                       # Pydantic API contracts
│       │   ├── services/                      # business logic — the only DB writers
│       │   │   ├── document_service.py · fact_service.py · task_service.py
│       │   │   ├── workspace_service.py · usage_service.py · notification_service.py
│       │   ├── ai/
│       │   │   ├── llm/
│       │   │   │   ├── base.py                # LLMProvider protocol, LLMResponse
│       │   │   │   ├── anthropic.py · openai.py · gemini.py
│       │   │   │   ├── router.py              # ModelTier → provider+model
│       │   │   │   └── client.py              # call_llm(): retry, budget, usage, span
│       │   │   ├── prompts/                   # versioned .md — the adaptation surface
│       │   │   │   ├── classification/v1.md
│       │   │   │   ├── extraction/v1_core.md · v1_university_circular.md
│       │   │   │   ├── merge/v1.md · chat/v1.md · judge/v1.md
│       │   │   ├── extraction/
│       │   │   │   ├── schemas/               # Pydantic — the adaptation surface
│       │   │   │   │   ├── base.py            # Fact, DocumentAnalysis
│       │   │   │   │   ├── university_circular.py · registry.py
│       │   │   │   ├── extractor.py · merger.py
│       │   │   ├── alignment/                 # ← the crown jewels
│       │   │   │   ├── text_map.py            # word boxes, rotation normalization
│       │   │   │   ├── span_matcher.py        # rapidfuzz alignment
│       │   │   │   └── rects.py               # spans → line-grouped rectangles
│       │   │   ├── normalization/  dates.py · bangla.py · text.py
│       │   │   ├── confidence.py              # composite + isotonic calibration
│       │   │   ├── rag/
│       │   │   │   ├── router.py              # full-context vs retrieval (§10.1)
│       │   │   │   ├── retriever_lexical.py · retriever_vector.py
│       │   │   │   ├── fusion.py · reranker.py · context_builder.py · answer.py
│       │   │   ├── embeddings/  client.py · cache.py · chunker.py
│       │   │   └── security/  injection_detector.py · sanitizer.py
│       │   ├── pipeline/
│       │   │   ├── orchestrator.py            # the stage machine (§11.3)
│       │   │   └── stages/  validate · normalize · parse · ocr · segment
│       │   │              · classify · extract · align · chunk · embed · index
│       │   ├── documents/                     # format handling
│       │   │   ├── pdf.py · docx.py · image.py · text.py
│       │   │   ├── ocr/  tesseract.py · cloud.py · vlm.py · quality_gate.py
│       │   ├── worker/  main.py · settings.py · cron.py
│       │   ├── eval/  runner.py · metrics.py · judges.py · datasets.py · cli.py
│       │   └── db/migrations/                 # alembic
│       ├── tests/  unit/ · integration/ · security/ · fixtures/ · cassettes/
│       ├── pyproject.toml · Dockerfile
│
├── packages/
│   ├── api-client/          # generated from OpenAPI (openapi-typescript + openapi-fetch)
│   ├── ui/                  # shared shadcn components (only if a 2nd app appears)
│   └── config/              # eslint · tsconfig · tailwind presets
│
├── eval/
│   ├── datasets/            # committed JSONL gold labels
│   ├── fixtures/            # source PDFs + PROVENANCE.md
│   └── results/             # committed run outputs (diffable history)
│
├── infra/
│   ├── docker/  api.Dockerfile · web.Dockerfile
│   ├── fly/     fly.api.toml · fly.worker.toml
│   └── grafana/ dashboards/*.json
│
├── docs/
│   ├── 00-product · 10-architecture · 20-ai · 30-data-api
│   │   · 40-frontend · 50-security-ops · 60-delivery · 90-appendices
│   ├── adr/                 # 0001-canonical-pdf.md, 0002-facts-single-table.md, …
│   ├── runbooks/            # one file per alert
│   ├── EVALUATION.md        # ← generated, contains REAL numbers
│   └── ARCHITECTURE.md      # the diagram + a 2-page summary
│
└── scripts/  seed.py · generate_client.sh · label_tool.py · check_all.sh
```

### 21.2 Conventions

- **Python:** `snake_case` files, `PascalCase` models, `verb_noun` service methods. `ruff` (lint + format), `mypy --strict` on `ai/` and `services/`.
- **TypeScript:** `kebab-case` files, `PascalCase` components, hooks as `use-*.ts`. `strict` + `noUncheckedIndexedAccess`.
- **The layering rule:** routes → services → models. **Routes never touch the ORM directly; services are the only writers.** This is what keeps `workspace_id` filtering and audit logging in one place instead of scattered across 40 handlers.
- **Config:** one `Settings` class, `SecretStr` for secrets, fail-fast validation at import. Never `os.getenv()` outside `config.py`.
- **Prompts are files, not string literals.** Versioned, diffable, reviewable, and recorded on every `usage_records` row.

---

## 22–23. Development Phases & Milestones

Each milestone below is a shippable increment with a binary acceptance test.

### PHASE 0 — Foundation (week 1)

| M | Deliverable | Acceptance |
|---|---|---|
| **0.1** | Monorepo, Compose (PG+pgvector, Redis, MinIO), Makefile, `.env.example` | `make setup && make dev` works on a clean machine |
| **0.2** | FastAPI skeleton: settings, async DB, structlog, request-id, problem+json, `/health` | `GET /health/ready` returns 200 with all deps checked |
| **0.3** | Alembic + `users`/`workspaces`/`memberships` + **RLS policies** | Tenant-isolation test passes on all three tables |
| **0.4** | Auth: register, verify, login, refresh rotation, argon2, Google OAuth | Full auth flow green in integration tests |
| **0.5** | Next.js shell: layout, Tailwind tokens, shadcn, TanStack Query, auth pages | Login → dashboard round trip works |
| **0.6** | CI: lint, type, test, migration-check, gitleaks | All gates green on `main` |

> **Do not skip 0.3.** Retrofitting RLS after 20 tables exist is a multi-day slog and you will get it wrong.

### PHASE 1 — MVP (weeks 2–7)

| M | Deliverable | Acceptance |
|---|---|---|
| **1.1** | Storage adapter (MinIO/R2), upload endpoint, magic-byte validation, SHA-256 dedup, quarantine→promote | 25 MB PDF uploads; a duplicate returns 409 with the existing id |
| **1.2** | ARQ worker + `document_stages` machine + orchestrator + retry/idempotency | Kill the worker mid-pipeline; on restart it resumes at the failed stage |
| **1.3** | **Normalization**: DOCX/TXT/image → canonical PDF; page renders; **text map with rotation handling** | All 5 input types produce a canonical PDF; rotated-page unit test passes |
| **1.4** | Tiered OCR: text-layer gate → Tesseract `eng+ben` → quality gate; word boxes preserved | Scanned Bangla fixture yields text with word boxes and a confidence score |
| **1.5** | LLM abstraction: protocol, 2 adapters, tier router, `call_llm` with retry + `usage_records` | Every call writes a usage row with a correct cost; provider swap is a config change |
| **1.6** | Classification + core extraction schema + windowed extraction + merge | Extraction returns a schema-valid `DocumentAnalysis` on all 12 fixtures |
| **1.7** | **Span alignment + rects + confidence composite** | Alignment unit suite green; ≥ 90% of facts on clean fixtures reach `verified` |
| **1.8** | Date normalization (Bangla numerals, months, DMY, relative, ambiguity flags) | Date-normalization eval suite ≥ 0.90 accuracy |
| **1.9** | `document_facts` + analysis endpoints + `GET /documents/{id}` with stages | `/analysis` returns grouped facts with rects in one request |
| **1.10** | **Document viewer**: pdf.js, virtualization, highlight overlay, click-to-navigate | Playwright test: click deadline → page 3 → highlight visible in the expected region |
| **1.11** | Chunking, embeddings + cache, pgvector HNSW, lexical `tsvector`, RRF fusion | `GET /search` returns ranked results with per-retriever scores |
| **1.12** | Chat: full-context router, SSE streaming, typed citation events, refusal | Citation pill click navigates the viewer to the right page |
| **1.13** | Task generation (rules + LLM ordering), task CRUD, `source_fact_id` linkage | Tasks generated from a circular carry correct due dates and source links |
| **1.14** | Dashboard, document library, tasks, calendar, upload UX, processing view | All screens usable end to end; empty/loading/error states designed |
| **1.15** | Demo workspace + seed fixtures (no LLM calls) | `make seed && make dev` → demo works offline |
| **1.16** | **Eval harness + 40–60 labeled docs + `docs/EVALUATION.md`** | Real numbers committed for classification, deadlines, requirements, retrieval, citations |
| **1.17** | Security pass: rate limits, headers, injection defense + flags, authz matrix tests | Full `tests/security/` suite green |
| **1.18** | Deploy: Fly (api+worker), Vercel, Neon, Upstash, R2; smoke tests | Public URL works; the demo workspace loads for an anonymous visitor |

### PHASE 2 — Production hardening (weeks 8–13)
SSE progress · presigned upload (100 MB) · team invites + role UI · audit log + viewer · email (Resend) + reminders + digests + `.ics` feed · per-workspace cost budgets · OTel → Grafana Cloud + dashboards + alerts · backups + a **rehearsed restore drill** · fact review loop + acceptance-rate metric · ClamAV · `/admin/eval` labeling tool · Playwright suite in CI · k6 load test.

**Acceptance:** a 3-person workspace collaborates without data leaks; an alert fires and a runbook resolves it; a backup restore is verified with row counts.

### PHASE 3 — Advanced AI (weeks 14–24)
Contextual retrieval (A/B'd) · reranker (gated on measured Recall@k) · `bge-m3` benchmark · cross-document Q&A · workspace search · new verticals via the schema registry (contract, invoice, HR letter) · table extraction · **document comparison/diff** · Batch API for >50-page docs · nightly eval regression dashboard · TOTP 2FA.

**Acceptance:** every AI change ships with a measured before/after in `docs/EVALUATION.md`. **A change with no measurement does not ship in this phase** — that is the discipline the phase exists to demonstrate.

### PHASE 4 — SaaS business (months 6–12)
Stripe + metered billing off `usage_records` · public API + scoped keys · webhooks · Drive/Gmail/Slack/Notion integrations · admin console with audited impersonation · SSO (WorkOS) + SCIM · mobile PWA + push.

---

## 27. Hackathon Preparation Strategy

### 27.1 The reframe

**Four hours is not enough to build a product. It is enough to *adapt* one.** So the goal of pre-work is not "build DocFlow." It is: **build a document-intelligence platform whose only variable part is the domain schema and one UI panel.**

If, when the problem statement lands, you find yourself touching auth, upload, the pipeline, the viewer, or deployment — the pre-work failed.

### 27.2 Build before the hackathon (this is Phase 0 + Phase 1)

| Component | Why it must be pre-built |
|---|---|
| Auth + workspaces + RLS | 6+ hours, zero demo value |
| Upload + validation + storage | 3 hours |
| Pipeline + stage machine + retries | 8 hours |
| PDF normalization + text map + rotation | 6 hours, and full of subtle bugs |
| OCR tiering | 4 hours |
| LLM abstraction + tier routing + usage tracking | 4 hours |
| **Span alignment + rects** | 6 hours, and it is the demo's centerpiece |
| Viewer + highlight overlay + virtualization | 8 hours |
| RAG (chunk/embed/hybrid/fusion) | 6 hours |
| Task generation + calendar | 4 hours |
| Design system + shadcn + all shared components | 8 hours |
| Docker + CI + deploy | 5 hours |
| **`make seed` with pre-computed fixtures** | 2 hours — **the highest-leverage item on this list** |

### 27.3 The adaptation surface — exactly four files

Design so that a brand-new document domain is:

```
1. apps/api/src/docflow/ai/extraction/schemas/<domain>.py     # Pydantic fact payloads
2. apps/api/src/docflow/ai/prompts/extraction/v1_<domain>.md  # the prompt
3. apps/api/src/docflow/ai/extraction/schemas/registry.py     # +1 line to register
4. apps/web/components/facts/panels/<Domain>Panel.tsx         # the render panel
```

**Nothing else.** Because `document_facts` uses a `kind` + JSONB payload (§12.3), **a new domain requires no migration**. That single schema decision is what makes a 4-hour pivot possible, and it is the strongest argument for it.

Build **two** domains before the hackathon (university circular + one of invoice/contract) purely to prove the seam actually works. A registry with one entry is not a registry; it is a hardcode with extra steps.

### 27.4 The four hours

| Time | Do | Guardrail |
|---|---|---|
| **0:00–0:25** | Read the problem. Write down: what documents, what facts, what action. Map facts to existing `kind`s; only add a new kind if genuinely necessary. Sketch the output panel on paper. | **Do not open the editor yet.** 25 minutes of thinking saves 2 hours of rework. |
| **0:25–1:15** | Write the schema + prompt. Test with `python -m docflow.eval quick --doc samples/1.pdf` on 3 real sample documents. Iterate on the prompt, not the code. | If extraction isn't ~decent by 1:15, **simplify the schema** — cut fields. Never extend the deadline. |
| **1:15–2:15** | Build the render panel. Reuse `FactCard`, `ConfidenceBadge`, `CitationLink`. Register the document type. | Reuse only. Writing a new component here is a scope failure. |
| **2:15–2:50** | Process 5 real documents end to end. Fix what breaks. **Commit the results as seed fixtures** so the demo never depends on live inference. | |
| **2:50–3:20** | Polish: landing copy, empty states, the one screenshot that sells it. Deploy. | Deployment is already automated. If it isn't, that was a pre-work failure. |
| **3:20–3:50** | **Record the demo video.** Rehearse the 3-minute script twice. Update the README. | Record before you are finished polishing. A recorded good demo beats an unrecorded great one. |
| **3:50–4:00** | Submit. Stop touching the code. | |

### 27.5 Hackathon-specific pre-work

1. **`DEMO_MODE=1`** — serves pre-computed analyses from fixtures with no LLM calls. Judges' WiFi and provider rate limits will not be your failure mode.
2. **`make demo`** — one command, full stack, seeded, browser opens.
3. **A README template** with placeholders for problem, approach, and the architecture diagram — fill in 5 blanks, not 5 paragraphs.
4. **A rehearsed 3-minute demo script**: problem (20 s) → upload (20 s) → **the citation click** (30 s) → tasks and calendar (40 s) → architecture + eval numbers (50 s) → close (20 s).
5. **A pre-built architecture diagram** as an image in the repo.
6. **`docs/EVALUATION.md` with real numbers already in it.** No other hackathon team will have measured evaluation results. This is the single strongest differentiator available to you and it costs zero hackathon minutes.

### 27.6 What to cut if you fall behind

In order: reranking → workspace-scope chat → calendar → task generation → chat entirely. **Never cut:** upload, extraction, the viewer, or the citation click. Those four *are* the demo.

---

## 28. Portfolio Presentation Strategy

### 28.1 The README is the product

A reviewer gives you 90 seconds. Structure for that:

```
# DocFlow AI
> Turn documents into decisions, deadlines, and tasks — with every fact
> traceable to the exact line it came from.

[▶ 20-second GIF: click a deadline → PDF scrolls to page 3 → text highlights]

[Live demo](…) · [Architecture](docs/ARCHITECTURE.md) · [Evaluation](docs/EVALUATION.md)
[CI ✅] [coverage 84%] [license MIT]

## The problem with document AI
Most tools stop at "summarize" and "chat." DocFlow extracts typed, verified
obligations — and it refuses to show you a fact it cannot point to in the source.

## How it works
[architecture diagram]

## Measured results          ← this section is why you get the interview
| Task | Metric | Score | N |
|------|--------|-------|---|
| Document classification | macro-F1 | 0.xx | 60 |
| Deadline extraction | F1 | 0.xx | 120 |
| Date normalization | accuracy | 0.xx | 200 |
| Retrieval | Recall@10 | 0.xx | 120 |
| Citation verification | verified rate | 0.xx | 1,4xx |
| Faithfulness (Opus-5 judge, κ=0.xx vs human) | 1–5 | x.x | 100 |
Full methodology: docs/EVALUATION.md

## Engineering decisions        ← links to ADRs
Why one canonical PDF · Why facts live in one table · Why RLS under app filtering
· Why full-context beats RAG on short documents · Why ARQ over Celery
· Why citations are computed rather than trusted

## Run it locally
git clone … && make setup && make demo      # working seeded app in ~5 minutes
```

### 28.2 What signals seniority

| Signal | How it shows up here |
|---|---|
| **Measured, not asserted** | `docs/EVALUATION.md` with N, confidence intervals, and judge-vs-human κ |
| **Knowing when *not* to build** | "Top 10 things not to build" in the README; reranking deferred with a stated reason |
| **Security as architecture** | The tenant-isolation test suite; the capability-isolation stance on injection |
| **Honest failure modes** | "DocFlow does not claim immunity to prompt injection…"; documented degradation when OCR loses coordinates |
| **Cost consciousness** | The usage dashboard; per-document cost breakdown; the batch-API routing decision |
| **Operational maturity** | Runbooks, alerts tied to symptoms, a rehearsed restore drill |
| **Taste** | A UI that looks like Linear, not like a bootstrap template |

### 28.3 Artifacts to produce

- `docs/ARCHITECTURE.md` — one diagram plus two pages
- `docs/adr/` — 8–12 ADRs in the standard Context/Decision/Consequences form
- `docs/EVALUATION.md` — generated, with methodology and limitations stated
- `docs/runbooks/` — proves you have thought about 3 a.m.
- A 3-minute Loom walkthrough linked from the README
- A written post: *"Why your RAG system probably shouldn't retrieve"* or *"Computing citations instead of trusting them"* — either would get real engagement, and both are genuinely non-obvious

**Do not** include: a "100% test coverage" badge (nobody believes it), an AI-generated logo, or a roadmap promising features you will not build.

---

## 29. Future Startup Roadmap

| Horizon | Focus | Milestone that matters |
|---|---|---|
| **0–6 mo** | Wedge: BD students. Distribution via Facebook/Discord admission groups. Free tier only. | 1,000 MAU, 40% week-4 retention, extraction acceptance rate > 85% |
| **6–12 mo** | Monetize. $4.99/mo student tier (price for the local market, not for SF). University department pilots. | 100 paying users; 3 department pilots; CAC < 1 month of revenue |
| **12–18 mo** | Vertical expansion: SME contracts and compliance. Same pipeline, new schemas. Team plan $29/user/mo. | 20 paying teams; net revenue retention > 100% |
| **18–30 mo** | Platform: user-defined schemas, workflow automation, public API, integrations. SOC 2 Type I. | 5 customers > $10k ARR; API used by 3rd parties |
| **30 mo+** | The obligation graph: cross-document, organization-wide duty tracking with reporting. | Category definition, not feature competition |

**The strategic bet:** every document-AI company converges on chat. The durable position is *structured obligation extraction with verifiable provenance* — because that is what enterprises will eventually be required to audit. Build the audit trail before anyone asks for it.

**Sequencing risk to watch:** the student wedge has low willingness to pay and the SME market has high willingness but a longer sales cycle. Expect to run both for a period. Do not abandon the wedge before the SME motion is proven — the wedge is what generates the corpus and the eval data that makes the SME product good.

---

## 30. Technical Risks & Tradeoffs

### 30.1 Tradeoffs consciously accepted

| Decision | Gained | Paid | Reversibility |
|---|---|---|---|
| Canonical PDF for everything | One viewer, one citation model | LibreOffice in the image (~450 MB); imperfect DOCX fidelity | Easy — add a native path later |
| One `document_facts` table | No migration per fact type; one review UI | Weaker type enforcement inside JSONB | Medium — promoting a kind to its own table is mechanical |
| pgvector over a dedicated vector DB | One database, transactional consistency, no sync | Ceiling around 5–10 M chunks | Medium — dual-write during migration |
| ARQ over Celery | Async-native, simple, one Redis | Thin ecosystem, no canvas | Easy — stage machine is in Postgres |
| Postgres FTS instead of true BM25 | Zero infra | Weaker lexical ranking; no Bangla stemmer | Easy — `pg_search` is a drop-in |
| Own auth instead of Clerk | No vendor cost, portfolio value, one authz model | ~6 h to build, ongoing responsibility | Hard — user migration is painful |
| No LangChain | Full control of tokens, caching, retries | ~300 lines of adapters to maintain | Easy |
| Own citation alignment | Bounding boxes, verification metric, provider-independent | ~6 h and real edge cases | N/A — required for the product |
| Sentry-only observability in MVP | Zero ops | No custom dashboards until Phase 2 | Easy — OTel is instrumented from day one |

### 30.2 The 10 risks that could actually sink this

| # | Risk | P | Impact | Mitigation | Trigger to act |
|---|---|---|---|---|---|
| **1** | **Bangla OCR quality is worse than assumed**, making the wedge unviable | High | High | Tiered OCR with a quality gate; cloud fallback; honest page-level citation degradation | **Test Tesseract `ben` on 20 real scanned circulars in week 1, before building anything else** |
| **2** | **Span alignment fails on messy real documents** (multi-column, tables) → the core feature is unreliable | Med-High | Critical | Fuzzy matching with a threshold; three-tier citation status; never fake a highlight; measured verification ratio | Verification ratio < 0.85 on the eval set |
| **3** | **Extraction quality plateaus below usefulness** on real-world layout chaos | Medium | High | Vertical focus; type-specific schemas; user review loop; escalation to DEEP tier | Fact acceptance rate < 70% |
| **4** | **Date normalization is wrong often enough to destroy trust** — a wrong deadline is worse than none | Medium | High | Deterministic parsing; explicit ambiguity flags; always show `raw_date_text`; user confirmation for ambiguous dates | Normalization accuracy < 0.90 |
| **5** | **Scope creep — 30 features, none finished** | **High** | High | The MVP scoping rule (§6); the explicit "not building" list; time-boxed phases | More than 2 out-of-scope items in a week |
| **6** | LLM cost per document exceeds the model | Low-Med | Med | Caching, tier routing, dedup, budgets, batch API, quota-capped free tier | Blended cost/doc > $0.15 |
| **7** | Provider outage or breaking API change | Medium | Med | The provider abstraction is exactly for this; secondary provider configured and tested quarterly | Any outage > 30 min |
| **8** | pgvector performance degrades at scale | Low (MVP) | Med | `hnsw.iterative_scan`; `ef_search` tuning; partition by workspace; recall test in the eval suite | Recall@10 drops > 5 points, or p95 retrieval > 300 ms |
| **9** | Prompt injection produces a visible embarrassment | Medium | Med | Capability isolation caps the blast radius; detection + banner; the honest README statement | Any confirmed incident → publish a postmortem |
| **10** | **Solo-maintainer burnout / abandonment** | **High** | Critical | Phases end in shippable states; `make demo` keeps it revivable; the MVP is deliberately 5–7 weeks, not 6 months | Two weeks with no commits → cut scope, ship what exists |

**Risks 1, 5, and 10 are the real ones.** Six through nine are engineering problems with known solutions. One is an assumption you can invalidate in a day — so validate it first. Five and ten are about discipline, and the antidote to both is the same: **finish and ship each phase before starting the next.**

### 30.3 The single most important instruction to yourself

> **Run the Bangla OCR spike in week 1, before writing a line of application code.**
>
> Take 20 real scanned circulars, run Tesseract `ben`, and measure character accuracy and word-box quality by hand. If it is unusable, you learn in one day that the wedge needs cloud OCR from the start (and what that does to unit economics) — instead of discovering it in week 6 with the whole system built around an assumption that was never true.

---

*Next: [90-appendices.md](90-appendices.md) — A through J.*
