# DocFlow Contribution and License Audit

**Reviewed:** 2026-10-27 (Asia/Shanghai)
**Checkout:** `DocFlow-AI`, branch `portfolio/initial-validation`, HEAD `7c584c939aa9cb1e778b662a4b29a9aab7d42881`.
**State:** Local changes remain uncommitted; no remote write or staging was performed.

## A. Upstream project

This checkout is a fork of [A-K-M-Asifuzzaman/DocFlow-AI](https://github.com/A-K-M-Asifuzzaman/DocFlow-AI). The product's original Next.js interface, FastAPI services, data model, document pipeline, provider adapters, task flow, architecture, and original product documentation come from upstream. The portfolio does not claim that the application was built from scratch by the account owner.

The upstream `LICENSE` is MIT and remains unchanged. The original copyright statement remains in place. The repository retains a direct upstream link in its README. New local code, reports, architecture documentation, and screenshots are presented as local contributions inside that attributed fork; no license or copyright statement was replaced.

## B. Local changes evidenced by the current Git diff

### Engineering and correctness

- Python typing and lint/format corrections across the API and worker, including the ordering-property rename from the conflicting `PipelineStage.index` name to `position`.
- Windows compatibility adjustment in the Google-auth source-inspection test so the source is explicitly read as UTF-8.
- Deadline fix: the Stub Provider preserves explicit clock text; extraction passes date and time together into normalization; the UI formats deadline calendar labels in the normalization timezone and displays the raw source date/time.
- Regression assertions in backend unit and API integration tests, plus two frontend date/timezone cases.

### Test and developer tooling

- Added Vitest and ESLint configuration, frontend test files, and required dependencies; the existing Vitest test script is now backed by the added runner, and the lint script changed from `next lint` to `eslint .`.
- The frontend suite now has 7 unit cases. Current checks passed; exact results are in `FINAL_LOCAL_QA.md`.
- Focused `package.json` / `package-lock.json` review (2026-10-27): the five new dev dependencies are `@eslint/eslintrc`, `@eslint/js`, `eslint`, `eslint-config-next`, and `vitest`. Their use is evidenced by `eslint.config.mjs`, the `eslint .`/`vitest run` scripts, and the frontend tests. Semantic lockfile comparison found 396 added package records, zero removed records, and zero version changes among common records; only the root dev-dependency list and `node_modules/semver` optional/dev metadata changed. Eighty additions are marked optional; 60 are platform-scoped records, chiefly esbuild/Rollup binaries. The default text diff reports 8,340 additions/2,222 deletions, while patience/histogram reports 6,120 additions/2 deletions; the JSON comparison shows the apparent deletion volume is diff-alignment noise around inserted lock entries, not removed dependencies. No install or tests were run during this focused review; prior validation remains documented separately in `FINAL_LOCAL_QA.md`.

### Portfolio evidence

- Rewrote `README.md` in English to distinguish the upstream product from local work, show actual validation and limitations, and link to the date-corrected captures.
- Added `docs/architecture.mmd` and four screenshot files. Screenshots 01 and 02 remain unchanged historical evidence; 03 and 04 are fresh captures from the real local application.
- Added date investigation, QA, baseline and second-stage reports under `reports/validation/`.

No additional worktree paths are intentionally categorized as unrelated. `apps/web/dbg.mjs` is an unchanged tracked file in HEAD, not a contribution in this diff; its key-shaped literal is recorded separately in `DocFlow-AI` without copying the value.

## C. Work not attributable to this local change

The seeded sample corpus, document processing features, dashboard, citation model, database schema, authentication flows, upstream provider implementations, and original product behavior predate this local diff. The project is a fork, not a personal end-to-end product build. The screenshots verify a local Stub Provider path only and do not establish production users, deployment, or paid-model performance.

## D. Previously recorded validation evidence (not rerun in this focused review)

- Prior recorded backend run: 347 non-eval tests passed.
- Prior recorded frontend run: 7 Vitest tests, TypeScript, ESLint, and production build passed; ESLint/build retain one custom-font warning.
- Prior recorded Ruff lint/format, mypy, and 17 Mermaid blocks passed.
- Date regression API value and source time are asserted by tests; updated UI captures show 15 September 2026 against the original source.
- Prior complete gate matrix and unrun scope: `FINAL_LOCAL_QA.md`; no gate was rerun in this focused review.
- Date root cause and minimal correction: `DATE_DISCREPANCY_INVESTIGATION.md`.

## E. Attribution and review limits

The upstream MIT license and notice are preserved, but this report is not legal advice. The public fork permission requirements should be reviewed before any publication of the local diff. The upstream copyright is not replaced. No GitHub remote settings, branches, or source were changed. The full public history and account-wide source security scan remain outside this contribution audit; see the security limitations report.

## F. Focused continuation review — 2026-10-27

- `apps/web/dbg.mjs:17` remains unchanged and tracked. Its `apiKey` field contains a Google API-key-shaped literal; this format match alone does not confirm a valid key, credential exposure, or leakage. The literal was not printed, logged, or copied into this report.
- The application Firebase module reads `NEXT_PUBLIC_FIREBASE_API_KEY` and related settings from environment variables. Those process variables were unavailable for comparison, no matching key was found in other tracked application source, and no standard tracked Firebase project configuration file (`.firebaserc`, `firebase.json`, or Google service config) was found. `.env` was not read. Console-side project ownership, key validity, API restrictions, and application/referrer restrictions remain `UNVERIFIED`.
- Node `v24.22.1` and npm `11.16.0` were checked. The prior QA results were not rerun; no additional tests were needed for this read-only security/lockfile review.
- No files were staged; no commit, push, PR, cloud change, or remote-setting change was made.


## G. Full worktree change inventory — 2026-10-27

The worktree has 24 modified tracked files and 14 non-ignored untracked files. No path is deleted or renamed; the index has no staged entries. The complete list and purpose follows.

### Modified tracked files (24)

| Path | Purpose of current worktree change |
|---|---|
| `README.md` | Replaces upstream marketing/evaluation claims with fork attribution, local validation scope, current demo screenshots, setup steps, and explicit limitations; removes the old demo sign-in and hosted deployment details from the current README. |
| `apps/api/src/docflow/ai/embeddings/embedder.py` | Renames local vector variables for clarity; no intended behavior change. |
| `apps/api/src/docflow/ai/extraction/extractor.py` | Passes extracted time-of-day text together with a deadline date into normalization. |
| `apps/api/src/docflow/ai/llm/client.py` | Adds an explicit embedding-provider type cast and removes a type suppression. |
| `apps/api/src/docflow/ai/llm/stub_provider.py` | Detects and preserves explicit time-of-day text in deterministic demo deadlines. |
| `apps/api/src/docflow/ai/normalization/dates.py` | Removes two parser argument type suppressions; no intended date-policy change. |
| `apps/api/src/docflow/api/v1/deps.py` | Removes a no-untyped-definition suppression. |
| `apps/api/src/docflow/api/v1/routes/dashboard.py` | Ruff-style expression formatting; no intended behavior change. |
| `apps/api/src/docflow/api/v1/routes/documents.py` | Uses the renamed pipeline-stage `position` property when ordering stages. |
| `apps/api/src/docflow/core/blobstore.py` | Formatting-only SQL call layout changes. |
| `apps/api/src/docflow/db/migrations/env.py` | Removes no-untyped-definition suppressions from migration callbacks. |
| `apps/api/src/docflow/documents/pdf.py` | Removes a type-argument suppression. |
| `apps/api/src/docflow/models/enums.py` | Renames `PipelineStage.index` to `position` to avoid colliding with the string enum's `index` method. |
| `apps/api/src/docflow/pipeline/orchestrator.py` | Updates stage comparisons to use `position`. |
| `apps/api/src/docflow/services/document_service.py` | Adds an explicit result cast for static typing. |
| `apps/api/src/docflow/services/google_auth.py` | Formatting-only URL and error-message line wrapping. |
| `apps/api/src/docflow/services/notification_service.py` | Adds a typed cursor-result cast for row-count handling. |
| `apps/api/tests/integration/test_api.py` | Adds a 5:00 PM sample deadline and asserts normalized UTC value and preserved time text. |
| `apps/api/tests/security/test_google_auth.py` | Reads inspected source as UTF-8 for Windows locale compatibility. |
| `apps/api/tests/unit/test_extraction.py` | Adds the explicit time to the sample and regression assertions for Dhaka timezone conversion. |
| `apps/web/components/facts/FactCard.tsx` | Shows raw deadline time beside raw date in the fact detail. |
| `apps/web/lib/utils.ts` | Formats deadline calendar dates in an explicit timezone, defaulting to Asia/Dhaka, with an override for tests/callers. |
| `apps/web/package.json` | Changes the lint script to ESLint and adds ESLint flat-config/Vitest development dependencies; the Vitest test script already existed in the baseline. |
| `apps/web/package-lock.json` | Locks those direct and transitive development-tool dependencies; see the dependency and registry review below. |

The Python typing and formatting edits are validation-gate cleanup, not new product features. Review them separately from the deadline behavior fix when preparing any future change set.

### Untracked files (14)

| Path | Purpose |
|---|---|
| `apps/web/eslint.config.mjs` | ESLint flat configuration with the Next.js ruleset. |
| `apps/web/vitest.config.ts` | Vitest configuration for web unit tests. |
| `apps/web/tests/lib/types.test.ts` | Tests terminal document-state handling. |
| `apps/web/tests/lib/utils.test.ts` | Tests utility functions and deadline timezone formatting. |
| `docs/architecture.mmd` | Editable architecture diagram source. |
| `docs/assets/01-workspace.png` | Historical seeded-workspace screenshot. |
| `docs/assets/02-citation-detail.png` | Historical citation screenshot; it shows the earlier one-day date mismatch. |
| `docs/assets/03-workspace-date-fixed.png` | Current seeded-workspace screenshot after the local date fix. |
| `docs/assets/04-citation-date-fixed.png` | Current citation/source screenshot after the local date fix. |
| `reports/validation/BASELINE_VALIDATION.md` | Preserved baseline validation record and dated addendum. |
| `reports/validation/CONTRIBUTION_AUDIT.md` | This attribution and worktree audit. |
| `reports/validation/DATE_DISCREPANCY_INVESTIGATION.md` | Date discrepancy evidence and correction rationale. |
| `reports/validation/FINAL_LOCAL_QA.md` | Historical local QA commands/results and scope limits. |
| `reports/validation/SECOND_STAGE_REVALIDATION.md` | Preserved prior-stage validation record. |

No changed or untracked file has a log, database, database dump, cache, or temporary-data extension. Environment files were excluded from content scanning; `.env` was not read. The untracked validation reports contain local absolute checkout-path references; sanitize them to repository-relative wording or keep them internal before public release.

## H. Final review disposition

The full file inventory is attributable to date correctness, type/lint cleanup, frontend test tooling, and portfolio documentation/evidence. The release gate remains `UNVERIFIED`: `apps/web/dbg.mjs:17`, the project-specificity of the external endpoint at `docs/DEPLOYMENT.md:92`, and the demo sign-in lines in the baseline README require private owner verification. `docs/DEPLOYMENT.md:64,66` are URI examples with placeholder components, not confirmed credentials. See the security limitations and commit readiness reports for scan scope and GitHub organization actions.
