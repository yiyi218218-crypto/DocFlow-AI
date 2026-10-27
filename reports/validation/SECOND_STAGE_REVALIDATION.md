# DocFlow AI Second-Stage Revalidation

**Date:** 2026-10-27
**Result:** PASS for the supported local path, with MinIO/S3 and OCR limitations recorded below.

## Scope and repository state

- Repository: DocFlow-AI
- Branch: portfolio/initial-validation
- HEAD: 7c584c939aa9cb1e778b662a4b29a9aab7d42881
- The branch and commit still match the task brief.
- At the start of this stage, the only existing worktree item was the untracked reports directory containing the prior baseline report. It was preserved. No user source edits were overwritten.
- The checkout is a fork of A-K-M-Asifuzzaman/DocFlow-AI under the upstream MIT license. The upstream license and copyright text were left unchanged.
- At the time this report was recorded, the local changes listed below were uncommitted review changes. No commit, push, merge, release, GitHub setting change, or other remote write had occurred at that time.
- The local .env file was not opened or modified. Validation used explicit non-secret environment overrides; DEMO_MODE=true selected the Stub Provider, confirmed in logs.

## Environment

- Windows PowerShell, Node v24.19.0, npm 11.16.0
- Python 3.12.12, uv 0.13.6, Docker Compose 5.5.1
- Next.js 15.5.23, ESLint 9.39.4, eslint-config-next 15.5.23, Vitest 3.2.4
- Backend validation ran from apps/api against isolated ports 5433 (PostgreSQL) and 6380 (Redis), with STORAGE_BACKEND=postgres, DEMO_MODE=true, and a non-secret test Firebase project ID. The uv command below refers to the temporary uv 0.13.6 executable used for this checkout.
- PostgreSQL pgvector/pgvector:pg16 and Redis 7-alpine ran in isolated Compose project docflow-revalidation.
- The project volume docflow-revalidation_postgres-data was preserved after stopping the containers.

## Runtime and workflow verification

- Fresh isolated PostgreSQL and Redis containers became healthy.
- Alembic applied all four migrations; current revision is 0003_blob_store (head). Windows PowerShell could not read the repository Alembic INI under the machine's cp936 locale, so an ASCII temporary config with absolute paths was created under the system temp directory. The tracked Alembic config was not changed.
- The first seed attempt processed three PDFs, then Windows cp936 could not print the Bengali sample filename. Re-running the idempotent seed command in Python UTF-8 mode completed successfully.
- Seed result: 4 documents and 12 generated tasks. Logs showed provider=stub, stub=True, and cost_usd=0.0.
- API health endpoint returned HTTP 200 with status ok. The readiness endpoint returned HTTP 200 with database=true, redis=true, and storage=true.
- Using the published demo login, the API returned 4 documents. The dashboard returned 13 tasks total: 12 open and 1 done. Seed logs report 12 generated tasks; the dashboard also includes the completed demo task.
- The production Next.js server returned HTTP 200 for the home page and rendered the DocFlow title.

## Quality gates

| Check | Result | Evidence |
|---|---|---|
| Alembic upgrade and current | PASS | Four migrations applied; 0003_blob_store (head). |
| API tests excluding paid evals | PASS | uv run pytest -q -m "not eval": 347 passed in 35.40s. This passed under the normal Windows locale after the source-inspection test was made explicit about UTF-8. |
| Ruff lint | PASS | uv run ruff check src tests: all checks passed. |
| Ruff format | PASS | uv run ruff format --check src tests: 104 files already formatted. |
| mypy | PASS | uv run mypy src: no issues in 88 source files. |
| Web unit tests | PASS | npm test: 2 files, 5 tests passed. |
| Web ESLint | PASS WITH WARNING | npm run lint exits 0: zero errors and one existing custom-font warning in app/layout.tsx. |
| Web TypeScript | PASS | npm run typecheck exits 0. |
| Web production build | PASS WITH WARNING | npm run build completed and generated all pages; it reports the same existing custom-font warning. |
| Mermaid diagrams | PASS | npm run check:mermaid: 17/17 blocks parsed. |
| Git whitespace check | PASS | git diff --check exits 0. |
| Playwright E2E | NOT RUN | The stage's core path was checked with the real API, seeded documents, task dashboard, health endpoints, and backend integration tests. |
| Paid provider evaluation | NOT RUN | The Makefile identifies make eval as a paid-provider suite; it was not run. |

## Minimal fixes made

1. Added Vitest 3.2.4, a focused Vitest config, and five unit tests for class merging, byte formatting, deadline urgency, Bengali detection, and terminal document states. The existing web test script already called Vitest but the package was missing.
2. Replaced the deprecated interactive Next.js lint wrapper with the ESLint CLI. Added ESLint 9, the matching eslint-config-next 15.5.23, and a FlatCompat configuration based on the official Next.js 15 config. The generated public/pdf.worker.min.mjs file is excluded from linting; it produced thousands of irrelevant diagnostics as a minified vendored worker. One existing layout font warning remains.
3. Resolved the Python type issues without weakening checks: renamed PipelineStage.index to position to avoid the inherited str.index signature conflict, updated its consumers, made SQL/provider/ORM result types explicit, separated a reused vector variable, and removed obsolete ignores.
4. Applied Ruff's requested formatting to dashboard.py, blobstore.py, and google_auth.py.
5. Made the source-inspection security test read UTF-8 explicitly so it passes under Windows' default locale.
6. Corrected the README seed command comment from 3 to 4 processed documents; the source sample list and successful seed run both confirm four.

No feature rewrite or broad dependency version upgrade was made. The existing locked dependency versions were not changed; the npm lockfile adds the ESLint/Vitest dependency graph.

## Known limits and unverified paths

- PostgreSQL blobstore is the storage backend verified in this run. MinIO/S3 was NOT RUN. The prior baseline report records that the configured MinIO Docker Hub image references could not be fetched in this environment; this run did not retry MinIO and does not claim S3 compatibility was validated.
- OCR is unavailable because a usable OCR engine is not installed. API startup logged startup.ocr_unavailable with impact scanned and Bangla pages. The Bangla seed PDF had no extracted facts when OCR was unavailable. Text-layer English PDFs completed; scanned image OCR and Bangla OCR remain unverified.
- The repository's OCR behavior for supported formats needs a separate dependency-equipped run before making a broader OCR claim.
- npm 11 reported that install scripts for several dependencies were not approved. The unit tests, lint, typecheck, and production build all completed successfully in that environment.

## Changed files and review evidence

- Python code: apps/api/src/docflow/ai/embeddings/embedder.py, apps/api/src/docflow/ai/llm/client.py, apps/api/src/docflow/ai/normalization/dates.py, apps/api/src/docflow/api/v1/deps.py, apps/api/src/docflow/api/v1/routes/documents.py, apps/api/src/docflow/api/v1/routes/dashboard.py, apps/api/src/docflow/core/blobstore.py, apps/api/src/docflow/db/migrations/env.py, apps/api/src/docflow/documents/pdf.py, apps/api/src/docflow/models/enums.py, apps/api/src/docflow/pipeline/orchestrator.py, apps/api/src/docflow/services/document_service.py, apps/api/src/docflow/services/google_auth.py, apps/api/src/docflow/services/notification_service.py, and apps/api/tests/security/test_google_auth.py.
- Runtime documentation: README.md, one factual demo document count correction (3 to 4).
- Web tooling and tests: apps/web/package.json, apps/web/package-lock.json, apps/web/eslint.config.mjs, apps/web/vitest.config.ts, apps/web/tests/lib/utils.test.ts, and apps/web/tests/lib/types.test.ts.
- git diff --check passed. At the time this report was recorded, all changes remained local and uncommitted.

## Stage decision

Stage 3 validation and its evidence-supported quality-gate repairs are complete. Per the task's stop rule, work stops here. README rewrites, screenshots, GitHub profile plans, GitHub metadata edits, commits, and pushes have not been started. The earlier account audit and scorecard/strategy remain in DocFlow-AI
