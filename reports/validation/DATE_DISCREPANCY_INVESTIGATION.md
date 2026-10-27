# Date Discrepancy Investigation

**Checked:** 2026-10-27 (Asia/Shanghai)
**Result:** Root cause reproduced and corrected locally; regression checks pass.
**Repository:** `DocFlow-AI`, branch `portfolio/initial-validation`, HEAD `7c584c939aa9cb1e778b662a4b29a9aab7d42881`.

## Evidence and data path

1. **Source document.** The seeded University of Dhaka sample in `apps/api/src/docflow/scripts/seed.py` says the deadline is `15 September 2026 at 5:00 PM`. The former screenshot `docs/assets/02-citation-detail.png` showed the same quote but a normalized display of 16 September.
2. **Extraction schema and Stub Provider.** `DeadlineFact.time_of_day_text` already existed in `apps/api/src/docflow/ai/extraction/schemas/base.py`. The Stub Provider previously extracted a date but dropped the time. The extraction layer then normalized only `raw_date_text`, so the source's explicit 5:00 PM was lost.
3. **Normalization.** `apps/api/src/docflow/ai/normalization/dates.py` uses `Asia/Dhaka` as its default normalization timezone. With no time supplied, a date-only deadline is represented at 23:59:59 in that zone. That instant is `2026-09-15T17:59:59Z`.
4. **Persistence and API.** Deadline values are stored as timezone-aware timestamps (`DateTime(timezone=True)`). PostgreSQL/asyncpg serializes the corrected 5:00 PM Dhaka instant as `2026-09-15T11:00:00Z`; the date is not stored as a date-only value. The integration test now asserts that API value and the preserved `5:00 PM` source field.
5. **UI.** The old frontend formatted the instant in the viewer's local timezone. In Asia/Shanghai, 23:59:59 Dhaka is already the next calendar day. A pure timezone-formatting reproduction confirmed the 16 September label. The UI now formats absolute deadline calendar labels in `Asia/Dhaka` by default. The fact card also shows the raw source date and time together.

## Root cause

Two omissions combined: the extraction path discarded an explicit source time, and the UI rendered an end-of-day Dhaka timestamp in the viewer's timezone. This caused the old sample's 15 September source date to appear as 16 September in Shanghai. The citation alignment indicator only verifies quoted text alignment; it does not validate normalized date semantics.

## Local correction

- `apps/api/src/docflow/ai/llm/stub_provider.py` recognizes and preserves explicit clock text such as `5:00 PM`.
- `apps/api/src/docflow/ai/extraction/extractor.py` passes the date and extracted time together to date normalization.
- `apps/web/lib/utils.ts` formats deadline calendar dates in `Asia/Dhaka`, with an optional timezone argument for explicit callers/tests.
- `apps/web/components/facts/FactCard.tsx` displays the source's raw date and time beside the normalized date.
- Backend unit/integration fixtures now include an explicit 5:00 PM and assert both the normalized instant and raw time. Two frontend regression cases cover viewer timezone differences and the absolute deadline label.

## Reproduction and acceptance

- Before correction, the old screenshot displayed `16 Sept 2026` for the seeded source date `15 September 2026`.
- After correction, the isolated seeded database's API flow returns `2026-09-15T11:00:00Z` with `time_of_day_text=5:00 PM`.
- The UI reproductions in `docs/assets/03-workspace-date-fixed.png` and `docs/assets/04-citation-date-fixed.png` display 15 September, and the latter shows the source quote highlighted.
- The full backend suite passed 347 tests; the frontend suite passed 7 tests. See `FINAL_LOCAL_QA.md` for the full gate matrix.
- Original screenshots 01 and 02 were retained unchanged; the images were not edited to conceal the discrepancy.

## Remaining limits

This fix establishes the sample's intended timezone through the existing Dhaka normalization default. It does not prove correct interpretation for documents with an unknown timezone, ambiguous abbreviations, or unsupported multilingual time expressions. Those cases need explicit timezone context or human review.
