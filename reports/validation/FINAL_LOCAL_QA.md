# Final Local QA

**Run date:** 2026-10-27 (Asia/Shanghai)
**Repository:** `DocFlow-AI`, branch `portfolio/initial-validation`, HEAD `7c584c939aa9cb1e778b662a4b29a9aab7d42881`.
**Mode:** Local-only; Stub Provider; no paid evaluation; isolated database `docflow_portfolio_datefix` on the existing `docflow-revalidation` PostgreSQL/Redis services.
**History boundary:** The results below are checks run during this continuation. Older checks are separately identified in `SECOND_STAGE_REVALIDATION.md` and are not re-labelled as fresh results.

## Quality gate results

| Gate | Result | Current-run evidence |
|---|---|---|
| Backend unit + integration suite (`pytest -q -m 'not eval'`) | **PASS** | 347 passed in 40.13 seconds. Eval tests excluded. |
| Ruff lint (`ruff check .`) | **PASS** | All checks passed. |
| Ruff format (`ruff format --check .`) | **PASS** | 104 files already formatted. |
| mypy (`mypy src`) | **PASS** | No issues in 88 source files. |
| Frontend unit tests (`npm test -- --reporter=dot`) | **PASS** | 2 test files, 7 tests passed, including deadline timezone cases. |
| TypeScript (`npm run typecheck`) | **PASS** | Command exited 0. |
| ESLint (`npm run lint`) | **PASS with warning** | 0 errors; one existing `@next/next/no-page-custom-font` warning at `apps/web/app/layout.tsx:45`. |
| Production build (`npm run build`) | **PASS with warning** | Build generated 9 routes; the same custom-font warning appeared. |
| Mermaid (`node scripts/check-mermaid.mjs`, from repository root) | **PASS** | 17/17 Markdown diagram blocks parsed. |
| Git whitespace check (`git diff --check`) | **PASS** | No whitespace errors. Git printed a working-copy LF/CRLF conversion warning for README.md. |
| Isolated database migration | **PASS** | New demo database reached `0003_blob_store` without altering the pre-existing database volume. A temporary ASCII Alembic config outside the repository was used because the machine locale could not decode punctuation in the repository config. |
| API liveness and readiness | **PASS** | `/health` returned `ok`; `/health/ready` returned `ok` for database, Redis, and storage. |
| Seeded end-to-end demo path | **PASS** | Stub seed generated 4 documents and 12 tasks. The first seed attempt hit a Windows GBK console encoding error while printing a Bengali filename; rerunning in UTF-8 completed successfully. No paid provider was used. |
| Browser demonstration flow | **PASS** | Local Chrome/Playwright automation signed into the seeded demo, opened the workspace and source document, selected the deadline citation, and captured current screenshots. This is screenshot validation, not the project's E2E test suite. |

## Current screenshots and data

- `docs/assets/03-workspace-date-fixed.png` shows the live local workspace and seeded tasks.
- `docs/assets/04-citation-date-fixed.png` shows the deadline card at 15 September 2026 and highlights the source line containing 15 September 2026 at 5:00 PM.
- The integration test asserts API `due_at` starts with `2026-09-15T11:00:00Z` and the payload preserves `time_of_day_text=5:00 PM`.
- Earlier screenshots `01-workspace.png` and `02-citation-detail.png` remain unchanged; 02 is historical evidence of the original mismatch.

## Not run / not established

| Path | Result | Limitation |
|---|---|---|
| Playwright E2E test suite | **NOT RUN** | A browser was used only to capture real screenshots; `npx playwright test` was not run. |
| MinIO/S3 | **NOT RUN** | This run used the PostgreSQL blob store. |
| OCR and scanned documents | **NOT RUN** | No OCR engine was installed/validated in this run. |
| Bangla OCR/recognition | **NOT RUN** | The seeded Bangla document is not evidence of a working OCR path. |
| Paid-provider model evaluation | **NOT RUN** | Stub Provider only; no paid model/API call was made. |
| Clean one-command Windows setup | **NOT RUN** | Direct isolated migrations and services were used; repository Makefile setup was not rerun end-to-end. |
| Full history/account secret scan | **NOT RUN** | See `SECURITY_AUDIT_LIMITATIONS.md`; `.env` files and Git history were excluded from the narrow current-content scan. |

## Operational cleanup and preservation

The API and Next.js dev server started for the screenshot capture were stopped. The `docflow-revalidation` Postgres/Redis containers were stopped without removing them; volume `docflow-revalidation_postgres-data` remains present. The newly added demo database is separate from prior validation data. No database volume or user file was deleted. No `git add`, commit, push, PR, or GitHub setting change was performed.

## Result

**Core local demo path: PASS.** The date regression is reproduced, corrected, and covered by tests and current screenshots. This is not a claim that all date formats, storage backends, OCR paths, paid models, or E2E behavior are validated.
