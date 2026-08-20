# DocFlow AI — Planning Blueprint

> **Turn documents into decisions, deadlines, tasks, and actions.**
>
> Status: **planning complete, implementation not started.**

This repository currently contains the complete technical and product blueprint for DocFlow AI. No application code has been written yet — that begins when you say so.

---

## Read in this order

| Doc | Sections | Contains |
|---|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | — | **All 12 architecture diagrams** — system, pipeline, OCR tiers, RAG routing, span alignment, injection defense, tenancy, deployment, CI, ER |
| [`docs/00-product.md`](docs/00-product.md) | 1–7 | Executive summary, vision, users, journeys, differentiation, **MVP scope**, roadmap |
| [`docs/10-architecture.md`](docs/10-architecture.md) | 8, 11, 15, 17, 26 | System architecture, document pipeline, background jobs, multi-tenancy, deployment |
| [`docs/20-ai.md`](docs/20-ai.md) | 9, 10, 19, 20 | Extraction architecture, RAG, **evaluation framework**, cost model |
| [`docs/30-data-api.md`](docs/30-data-api.md) | 12, 13 + C, D | Database schema, **ER diagram**, API contract, **endpoint map** |
| [`docs/40-frontend.md`](docs/40-frontend.md) | 14 | Frontend architecture, **document viewer deep-dive** |
| [`docs/50-security-ops.md`](docs/50-security-ops.md) | 16, 18, 24, 25 | Threat model, observability, testing, CI/CD |
| [`docs/60-delivery.md`](docs/60-delivery.md) | 21–23, 27–30 | Repo structure, phases, milestones, **hackathon strategy**, portfolio, risks |
| [`docs/90-appendices.md`](docs/90-appendices.md) | A–J | Final stack, architecture diagrams, checklists, what *not* to build |
| [`prompt.md`](prompt.md) | — | **Frontend implementation prompt** (skills setup + full brief) |

---

## The core invariant

Everything in this blueprint serves one thing:

> **The Provenance Chain**
> `document → page → character span → bounding box → extracted fact → generated task`
>
> Every extracted fact carries a verified quote from the source. If the quote cannot be verified against the source text, the fact is not shown as confirmed.

This is simultaneously the product, the trust model, the primary evaluation metric, and the anti-hallucination defense.

---

## The ten decisions that define the architecture

1. **Normalize every input to PDF + page image + word-level text map** — one viewer, one citation model, for every format.
2. **Citations are computed, not trusted** — fuzzy span alignment maps quotes to bounding boxes; unmatched quotes are flagged, never faked.
3. **Full-document context first, RAG second** — a 10-page circular fits in context; retrieval is for corpus scale, not document scale.
4. **Dates are normalized by code, not by the LLM** — `dateparser` + Bangla numeral folding, with explicit ambiguity flags.
5. **Confidence is computed and calibrated**, never self-reported by the model.
6. **One `document_facts` table, not five** — which is also what makes a 4-hour hackathon pivot possible with no migration.
7. **Postgres RLS beneath app-level tenant filtering** — a forgotten `WHERE` returns zero rows instead of another tenant's data.
8. **Pipeline state lives in Postgres; the queue is only delivery** — durable-execution semantics without Temporal.
9. **No LangChain** — thin native adapters behind a tier-based LLM protocol, so token accounting and caching stay under your control.
10. **Document content is untrusted input with zero capability** — the extraction call has no tools, no network, no database.

---

## Before writing any application code

**Run the Bangla OCR spike.** Take 20 real scanned circulars, run Tesseract `ben`, and measure character accuracy and word-box quality by hand.

The entire wedge depends on this being viable. It takes one day to find out, and it changes the architecture and unit economics if the answer is bad. Do it first — see [`docs/60-delivery.md`](docs/60-delivery.md) §30.3.

---

## Next steps

1. Review the blueprint; push back on anything you disagree with.
2. Run the Bangla OCR spike.
3. Say "start Phase 0" to begin implementation (see [`docs/60-delivery.md`](docs/60-delivery.md) §22).
4. For the frontend, hand [`prompt.md`](prompt.md) to Claude Code.
