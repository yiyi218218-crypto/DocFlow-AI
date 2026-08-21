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

## 4b. PDF coordinate mapping — verified against rendered pixels

**Measured 2026-08-21 — 18/18 passing** (`tests/unit/test_pdf_extraction.py`).

Asserting a rectangle "looks right" numerically is weak, because the numbers can
be self-consistently wrong. So these tests render the page to a bitmap and
measure what fraction of the page's **ink** falls inside the computed highlight.

| Page rotation | Ink inside the computed highlight |
|---|---|
| 0° | > 85% |
| 90° | > 85% |
| 180° | > 85% |
| 270° | > 85% |

This empirically confirms the rotation transform (`page.rotation_matrix` applied
before normalising against `page.rect`). Without it, highlights on rotated pages
— which scanners produce constantly — land at 90° offsets. The bug is invisible
in development and glaring to a user.

### Text-layer coverage — a corrected assumption

The design doc originally specified a native-text-layer gate of **"≥40% word-box
coverage"**. Measured on A4:

| Page content | Word-box coverage |
|---|---|
| Blank page | 0.000 |
| One line of text | 0.012 |
| Four lines | 0.025 |
| Dense full page | **0.309** |

A 40% gate would have routed **every real page** to OCR, including clean digital
PDFs — burning cost and destroying citation quality for no reason. The threshold
is now **0.005**, which still cleanly separates a blank/degenerate text layer
(0.000) from a sparse but real one (0.012). Docs corrected in
[10-architecture.md](10-architecture.md) §11.4 and [ARCHITECTURE.md](ARCHITECTURE.md) §4.

*This is the kind of thing the "measure, don't assume" discipline is for: the
number was plausible, cited in a design document, and wrong by ~50×.*

---

## 4c. The citation interaction, in a real browser

**Measured 2026-08-21 — 5/5 passing** (`apps/web/e2e/citation.spec.ts`, Chromium).

The differentiator, asserted end to end against the seeded demo workspace:

| Behaviour | Result |
|---|---|
| Sign in, list seeded documents | pass |
| **Click a deadline → source span highlights on the correct page** | pass |
| Highlight rectangle has a sane on-screen size (>20×4 px) | pass |
| Active fact mirrored to `?fact=&page=` | pass |
| Highlight restored after a full page reload | pass |
| Deadline shows the raw date text from the document | pass |
| Citation verification rate reported in the UI | pass (100%) |

Two real bugs surfaced only by running this in a browser:

- **pdf.js cannot send our in-memory bearer token.** It fetches its own URL, so
  pointing it at an authenticated endpoint returned 401 and rendered no viewer
  at all. The client now fetches the PDF itself and hands pdf.js a blob URL.
  This is exactly the failure mode [10-architecture.md](10-architecture.md) §26.2
  predicted ("this will be your first deploy bug") — it just happened locally first.
- **Document titles leaked the internal `[page N]` marker.** Those markers are
  injected so the model can attribute a quote to a page; a model echoing one back
  must never reach a user-facing field. Now stripped provider-agnostically.

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

## 6. Model selection — quality vs. cost, measured

**Measured 2026-08-21.** 3 documents, all tiers pointed at one model, live API.
`uv run python -m docflow.eval.model_bench`

The question that decides the model here is not general capability but **quote
fidelity**: a model that paraphrases its `source_quote` fails span alignment,
which demotes the fact, which removes the highlight. No public benchmark
measures that, so this one does.

| model | facts | citation verification | deadlines ✓ | cost (3 docs) |
|---|---|---|---|---|
| `gpt-4.1-nano` | 12 | **100%** | 2/3 | **$0.00205** |
| `gpt-4o-mini` | 8 | **100%** | 2/3 | $0.00207 |
| `gpt-4.1-mini` | 9 | **100%** | 2/3 | $0.00417 |
| `gpt-5-nano` | 0 | **0%** | 0/3 | $0.01131 |

**`gpt-5-nano` failed outright** — schema invalid even after the repair
round-trip on all three documents, while costing **5× more than the models that
worked**. It is a reasoning model: on a trivial extraction it emitted 433 output
tokens where `gpt-4o-mini` used 26, and it still got `raw_date_text` wrong
(returning the whole sentence). More expensive *and* worse is a useful result.

`gpt-4.1-nano` was independently observed corrupting a quote in a smoke test
("must be **be** submitted"). Fuzzy alignment absorbed it here, but the fidelity
margin is thinner than `gpt-4o-mini`'s exact reproduction — which is why
extraction runs on `gpt-4o-mini` and only the cheap mechanical tiers use nano.

**Chosen routing:** FAST `gpt-4.1-nano` · BALANCED `gpt-4o-mini` · DEEP
`gpt-4.1-mini`. Measured cost ≈ **$0.0007 per short document**.

*Caveat:* 3 documents. This is model selection, not the labelled evaluation set.
The 2/3 deadline score was a broken fixture, not a model failure — see §7.

---

## 7. Bangla — the wedge assumption, tested

> This is the week-1 spike [60-delivery.md](60-delivery.md) §30.3 said to run
> before anything else. It was run, and it inverted a core design assumption.

**Measured 2026-08-21.** Bengali notice → PDF → text layer vs. Tesseract `ben`.

| Source line | PDF text layer | Tesseract `ben` |
|---|---|---|
| ঢাকা বিশ্ববিদ্যালয় | 68.4 | **100.0** |
| আবেদনের শেষ তারিখ ১৫ সেপ্টেম্বর ২০২৬। | 70.3 | **100.0** |
| শিক্ষার্থীদের অবশ্যই একাডেমিক ট্রান্সক্রিপ্ট | 63.8 | **100.0** |
| জাতীয় পরিচয়পত্রের ফটোকপি প্রয়োজন। | 69.4 | **100.0** |
| **whole document** | **69.4** | **97.1** |

OCR mean word confidence **92.0**, with word boxes intact.

### Finding 1 — Tesseract `ben` is good, not "mediocre"

The blueprint rated Bengali OCR "mediocre but real" and treated it as the
project's biggest risk. On clean rendered Bengali it scores **100 per line**.
**The wedge is viable.**

### Finding 2 — for Bengali, the text layer is WORSE than OCR

This was not anticipated. Bengali conjuncts shape into ligature glyphs, and when
a font's ToUnicode CMap does not map them back, extraction emits Latin-1, Greek
and Latin-Extended substitutes *inside* otherwise-Bengali words:

```
source:    শিক্ষার্থীদের অবশ্যই একাডেমিক ট্রান্সক্রিপ্ট জমা দিতে হবে।
extracted: শিáা½থদের অবশɹই একাডেমিক ȵাĵ Úি Ƌ জম দিতে হবে।
```

The page passes every existing check — 201 characters, normal coverage, zero
"garbage" by the control-character definition — and every citation on it is
**UNVERIFIED**. A silent, total failure of the product's differentiator on its
primary language. PyMuPDF extraction flags do not fix it (70.3 → 73.0 at best).

**Architectural consequence.** "Prefer the native text layer, fall back to OCR"
is backwards for Bengali. The gate now detects *script corruption* — letters
outside the expected scripts appearing inside Bengali text — and routes such
pages to OCR **despite having a text layer**
(`ai/normalization/text.py::script_corruption_ratio`, threshold 0.05).

| Sample | corruption | routed to |
|---|---|---|
| Clean Bengali | 0.000 | native |
| **Mangled Bengali** | **0.186** | **OCR** |
| Clean English | 0.000 | native |
| Code-switched bn/en | 0.000 | native |

### End to end, on a Bangla circular

Full pipeline, real Postgres + MinIO + Tesseract:

| | |
|---|---|
| Text layer | detected corrupt → OCR |
| OCR confidence | 92.8 |
| Facts extracted | 3 |
| **Citation verification** | **3/3 verified** (scores 95, 100, 100) |
| Deadline | `১৫ সেপ্টেম্বর ২০২৬` → **2026-09-15** |
| Highlight rectangles | present on all three |

Also fixed here: `text_to_pdf` rendered Bangla as `?????` because no bundled
face covered the Bengali block. Noto Sans Bengali (OFL) is now embedded rather
than assumed, so behaviour is identical on macOS, the Debian image and CI.

*Caveats.* This is **rendered** Bengali, not a photographed or low-quality scan.
Real scanned government circulars will score lower, and that measurement still
requires the 20-document fixture set. What this establishes is that the approach
is sound and the failure mode is understood — not that the numbers hold on paper
scans.

---

## 8. Not yet measured

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
