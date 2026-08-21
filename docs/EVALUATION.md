# DocFlow AI — Evaluation

> **Status: partial.** This file records only what has actually been measured.
> Sections marked *not yet measured* are exactly that — they are listed so the
> gaps are visible, not to imply a result exists.
>
> Methodology and metric definitions: [20-ai.md](20-ai.md) §19.

Last updated: 2026-08-21 · commit: see `git log`

---

## 1. Embedding model — cross-lingual retrieval

**Measured 2026-08-21.** `text-embedding-3-small`, 1536 dimensions, live API call.

The wedge is Bangladeshi student documents, which are frequently Bangla or
code-switched, while users often ask questions in English. So the question that
matters is not "are these embeddings good" but "does an English query retrieve a
Bangla passage that says the same thing".

| Pair | Cosine similarity |
|---|---|
| *"Applications must be submitted by 15 September 2026."* vs. **the same sentence in Bangla** | **0.297** |
| *"Applications must be submitted by 15 September 2026."* vs. *"The cat sat on the mat."* (control) | 0.018 |

**Reading this honestly.** 0.297 is clearly above the ~0.02 noise floor, so the
model does carry *some* cross-lingual signal. But for two sentences with
identical meaning it is weak — a strong multilingual embedding model would be
expected in the 0.7+ range. In a hybrid retrieval setup where the vector arm is
supposed to catch what lexical matching misses, this is precisely the case it
would miss.

**Consequence for the roadmap.** This is direct evidence for the Phase 3 item in
[20-ai.md](20-ai.md) §10.6 — benchmark `BAAI/bge-m3` (multilingual, dense +
sparse, self-hostable) against the current model on a Bangla evaluation set.
The blueprint listed that as a hypothesis; it is now a measured gap.

**Caveats.** N = 1 sentence pair. This is a smoke measurement made while wiring
up the provider, not a benchmark. It is recorded because it is real and it
points somewhere, not because it is sufficient. A proper comparison needs the
retrieval suite below (120 query/document pairs, graded relevance) run against
both models.

---

## 2. Cost model

**Verified by test, not by production traffic.** `tests/unit/test_llm.py`
asserts the cost function against published August 2026 Anthropic pricing and
guards the blueprint's per-document estimate.

| Scenario | Modelled cost |
|---|---|
| 10-page document (3 extraction windows, cached prompt prefix, Haiku classify + merge) | **≈ $0.065** |
| Cache read vs. fresh input | read billed at **0.1×** input rate — asserted |
| Batch API | **0.5×** — asserted |

The test asserts the total lands in `$0.03–$0.12`, so a pricing or routing change
that moves per-document cost outside that band fails the build.

*Not yet measured:* actual cost per document on real files, which needs the
pipeline running end to end.

---

## 3. Tenant isolation

**Measured — 48/48 passing** (`tests/security/test_tenant_isolation.py`).

| Check | Coverage | Result |
|---|---|---|
| RLS enabled | all 13 tenant tables, parameterised from model metadata | pass |
| `tenant_isolation` policy present | all 13 | pass |
| App role is not table owner | all 13 | pass |
| Cross-tenant read (direct UUID / IDOR) | documents, tasks | denied |
| Cross-tenant UPDATE / DELETE | documents, tasks | 0 rows affected |
| Cross-tenant INSERT (forged `workspace_id`) | tasks | rejected by `WITH CHECK` |
| Unscoped session (no tenant GUC) | documents | 0 rows — fails closed |
| `usage_records` immutability | UPDATE as app role | permission denied |

The structural checks are parameterised over every model carrying
`WorkspaceScopedMixin`, so a new tenant table without a policy fails the build
rather than shipping a leak.

---

## 4. Span alignment and citation verification

**Measured on synthetic fixtures — 20/20 passing** (`tests/unit/test_span_alignment.py`).

| Behaviour | Result |
|---|---|
| Exact quote → `verified` with rectangles | pass |
| Whitespace / case variance → still `verified` | pass |
| Truncated quote → located | pass |
| **Fabricated quote → `unverified`** | pass — the anti-hallucination gate |
| Wrong claimed page → recovered on the correct page | pass |
| Too-short quote (`"2026"`) → rejected as non-evidence | pass |
| Multi-line span → one rectangle per visual line | pass |
| Page without word boxes → `page_only`, no fabricated highlight | pass |
| Offset map survives whitespace collapse and Bangla numeral folding | pass |

*Not yet measured, and this is the important gap:* **citation verification rate
on real documents.** The synthetic fixtures prove the algorithm behaves as
specified; they say nothing about how often a real model's quote aligns against
a real PDF's text layer. That number — the flagship metric — requires the
pipeline and the labelled fixture set.

---

## 5. Date normalization

**Measured — 30/30 passing** (`tests/unit/test_date_normalization.py`).

| Behaviour | Result |
|---|---|
| Six common formats resolve to the same day | pass |
| Bangla numerals + Bangla month (`১৫ সেপ্টেম্বর ২০২৬`) | pass |
| End-of-day default (so a deadline does not expire the night before) | pass |
| `05/09/2026` flagged **ambiguous** with both readings offered | pass |
| `15/09/2026` correctly *not* flagged | pass |
| Relative date without anchor → unresolved, not guessed | pass |
| Relative date with publication anchor → resolved, avoids BD weekend | pass |
| Bengali-calendar month → refuses rather than guesses | pass |
| Implausible year (2099 / 1998) → rejected | pass |

*Not yet measured:* accuracy on the 200-string `date_normalization` suite drawn
from real documents.

---

## 6. Not yet measured

Listed so the gaps are explicit rather than implied.

| Suite | Blocked on |
|---|---|
| Document classification (accuracy, macro-F1) | labelled fixture set |
| Deadline extraction (P/R/F1) | labelled fixture set + pipeline |
| Requirement / entity / action-item extraction | labelled fixture set + pipeline |
| Retrieval (Recall@k, MRR, nDCG) | chunk-level relevance labels |
| **Citation verification rate on real documents** | pipeline end to end |
| Faithfulness (judge + human κ) | 50 human-labelled items |
| Injection resistance | 25 adversarial fixtures |
| Bangla OCR character accuracy | **the week-1 spike — 20 real scanned circulars** |

> The Bangla OCR spike is the one that matters most. The entire wedge assumes
> Tesseract `ben` is usable on real scanned circulars, and that assumption is
> currently unvalidated. See [60-delivery.md](60-delivery.md) §30.3.

---

## How to reproduce

```bash
make setup          # infra + migrations
make test           # everything below except the live embedding check
uv run pytest tests/security/test_tenant_isolation.py -q
uv run pytest tests/unit -q
```

The embedding measurement in §1 requires `OPENAI_API_KEY` and makes a real API
call (~$0.000001). Every other number here runs offline and free.
