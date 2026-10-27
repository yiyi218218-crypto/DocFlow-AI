# DocFlow AI Baseline Validation

> This file preserves the initial validation record. Its original final-state notes describe that earlier run; the addendum below records the later authorized Stage 3 changes and current results.

**Date:** 2026-10-27
**Result:** PARTIAL — core app path validated; quality gates and exact MinIO path remain unresolved.

## Checkout and provenance

- Fork: https://github.com/yiyi218218-crypto/DocFlow-AI
- Upstream: https://github.com/A-K-M-Asifuzzaman/DocFlow-AI
- Local checkout: `DocFlow-AI`
- Branch: `portfolio/initial-validation`
- Baseline commit: `7c584c939aa9cb1e778b662a4b29a9aab7d42881`
- `origin` points to the fork; `upstream` points to the original repository.
- The baseline checkout was clean. No tracked source or upstream configuration was changed during validation.
- A local `.env` was created from `.env.example`; no provider API keys were added. `.env` is ignored by Git.

## Runtime and database

- Docker Compose project `docflow-validation` was used to isolate the local services and data volume.
- PostgreSQL: `pgvector/pgvector:pg16`, healthy; `vector` extension reports version `0.8.7`.
- Redis: `redis:7-alpine`, healthy; `redis-cli ping` returned `PONG`.
- MinIO was not started. The repository Compose references `minio/minio:latest` and `minio/mc:latest`, which failed to pull from Docker Hub. MinIO upstream's Compose/Helm files document Quay tags, but the tested Quay manifests returned `no such manifest` in this environment. The upstream reference example is [MinIO's Compose file](https://github.com/minio/minio/blob/master/docs/orchestration/docker-compose/docker-compose.yaml).
- To continue functional validation without changing the repository, the documented `STORAGE_BACKEND=postgres` option was used for API, worker, seed, and integration tests. The MinIO/S3 backend itself remains unverified.
- Alembic migration reached `0003_blob_store (head)`; all four migrations applied successfully. The repository `alembic.ini` contains UTF-8 punctuation that Windows' `cp936` locale could not decode. A temporary ASCII copy outside the repository, with absolute source paths, was used to run Alembic; the tracked config was not edited.
- API started on `127.0.0.1:8000`. `/health` returned HTTP 200; `/health/ready` returned HTTP 200 with database, Redis, and storage all `true`.
- Frontend production server started on `127.0.0.1:3000`; `/` returned HTTP 200 and the expected DocFlow page title.
- The project's `seed` command completed with 4 sample documents and 12 generated tasks. Logs show `demo_mode=True`, stub provider calls, and `$0.00` model cost; no paid AI API was used.
- API startup warned that OCR was unavailable. Scanned images and Bangla pages are affected; the seeded text-layer PDFs completed.

## Checks actually run

| Check | Result | Evidence |
|---|---|---|
| Web TypeScript | PASS | `npm run typecheck` exited 0. |
| Web production build | PASS | `npm run build` compiled and generated all 9 pages. |
| Mermaid diagrams | PASS | `npm run check:mermaid`: 17/17 blocks parsed. |
| Web unit tests | FAIL | `npm test` could not find `vitest`; `apps/web/package.json` does not declare it. |
| Web lint | BLOCKED | `npm run lint` reached an interactive request to configure ESLint and exited nonzero; lint rules were not run. |
| Python unit + integration tests (excluding eval) | PASS | `pytest -q -m "not eval"`: 347 passed. The test process used Python UTF-8 mode and a non-secret test Firebase project ID. |
| Python Ruff lint | PASS | `ruff check .`: all checks passed. |
| Python Ruff format | FAIL | `ruff format --check .` reports 3 upstream files would be reformatted: `src/docflow/api/v1/routes/dashboard.py`, `src/docflow/core/blobstore.py`, and `src/docflow/services/google_auth.py`. No formatter was run in write mode. |
| Python mypy | FAIL | 12 errors across 9 files: `models/enums.py`, `ai/normalization/dates.py`, `services/notification_service.py`, `db/migrations/env.py`, `documents/pdf.py`, `ai/llm/client.py`, `api/v1/deps.py`, `ai/embeddings/embedder.py`, and `services/document_service.py`. |
| Database migration | PASS | `alembic current` reports `0003_blob_store (head)`. |
| Eval suite | NOT RUN | The Makefile labels `make eval` as costing money; no real provider key was configured. |
| Full MinIO/S3 path | BLOCKED | Official image references could not be pulled/resolved; PostgreSQL blobstore was used for the successful app-path validation. |

The first unit-test attempt had two environment-dependent failures: Firebase project ID was unset, and Python used the Windows locale encoding for `Path.read_text()`. Re-running with a non-secret test project ID and `python -X utf8` passed all 268 unit tests; 79 integration tests also passed. The combined non-eval run then passed all 347 tests.

## Gate decision

**Stage 4 is not passed.** The frontend and API run, migrations are applied, seeded document processing succeeds in stub mode, and all 347 non-eval backend tests pass. However, web tests cannot run because Vitest is missing, ESLint is not configured, Ruff formatting reports existing differences, and mypy reports 12 errors. The MinIO/S3 path and scanned-page OCR are also unverified. The task requires these remaining mandatory issues to be accepted before the demo/portfolio stage.

Therefore no promotional screenshots, `docs/PORTFOLIO.md`, portfolio README changes, GitHub metadata edits, commit, or push were made. The GitHub portfolio audit was not started.

## Recommended follow-up

1. Decide whether to authorize fixes to the repository's web test/lint setup and existing Ruff/mypy findings. No source changes were made to mask these results.
2. Decide whether to repair the upstream MinIO Compose references or accept PostgreSQL blobstore as the supported local demo backend; verify S3 separately if it remains a requirement.
3. Install/configure OCR only if scanned or Bangla documents must be part of the demo.
4. Re-run the relevant checks and re-evaluate Stage 4 before creating screenshots or publishing portfolio claims.

## Cleanup and repository state

- API, worker, frontend server, PostgreSQL, and Redis were stopped after validation. The isolated Compose network/containers were removed with docker compose down; the docflow-validation_postgres-data volume was intentionally preserved.
- Final Git status: no source changes; only this validation report is new and uncommitted. The local .env, .venv, Node modules, and build artifacts are ignored.


## Revalidation addendum — 2026-10-27

The initial baseline above records the state before this task's authorized Stage 3 fixes. The following revalidation supersedes its earlier FAIL/BLOCKED results for Vitest, ESLint, Ruff formatting, and mypy:

- The isolated Compose project docflow-revalidation applied all four migrations through 0003_blob_store (head). PostgreSQL and Redis were healthy; the verified storage backend was the documented PostgreSQL blobstore.
- The seed workflow completed in Python UTF-8 mode: 4 documents and 12 generated tasks. Logs confirmed Stub Provider responses and $0.00 model cost. A first attempt stopped while printing a Bengali filename under Windows cp936; the idempotent UTF-8 rerun succeeded.
- API /health and /health/ready both returned HTTP 200. The readiness payload reported database, Redis, and storage true. Demo login and API reads returned 4 documents; the dashboard showed 13 total tasks, 12 open and 1 done. The built frontend home page returned HTTP 200.
- Final backend checks: 347 non-eval tests passed; Ruff lint and format passed; mypy reported no issues in 88 files.
- Final frontend checks: 5 Vitest tests passed, TypeScript passed, ESLint exited 0 with one existing custom-font warning, production build passed with the same warning, and 17/17 Mermaid blocks parsed.
- Minimal fixes are documented in SECOND_STAGE_REVALIDATION.md. The security source-inspection test now selects UTF-8 explicitly, and it passed without enabling Python UTF-8 mode.
- README.md's seed command comment was corrected from 3 to 4 processed documents, matching the seed source and successful run.
- MinIO/S3 and scanned/Bangla OCR remain unverified. OCR startup warnings were observed; the Bangla seed document produced no extracted facts without an OCR engine. No paid provider evaluation was run.
- The local environment file was not read or modified. The new Compose volume was preserved. No commit or remote write occurred.
- The checkout remains on portfolio/initial-validation at 7c584c939aa9cb1e778b662a4b29a9aab7d42881. Source and tooling changes are local and uncommitted. This report does not claim upstream code as original work.
- Per the task stop rule, no README rewrite, screenshots, profile plan, GitHub metadata edit, commit, or push was started.
