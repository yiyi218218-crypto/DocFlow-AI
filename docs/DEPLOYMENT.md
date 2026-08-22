# Deploying DocFlow AI for free

The whole stack is **one web service and one Postgres**. Two dependencies were
removed rather than paid for, and the removal is a config flag in both cases.

| Piece | Where | Cost | Why |
|---|---|---|---|
| Web app | Vercel | free | Next.js first-class, global CDN |
| API + pipeline | Render web service (Docker) | free | Runs the image as-is; Singapore region |
| Postgres + pgvector | Neon | free, no expiry | Render allows one free DB per account, and expires it after 30 days |
| Object storage | — none — | — | `STORAGE_BACKEND=postgres` |
| Redis / worker | — none — | — | `WORKER_MODE=inline` |

---

## What was removed, and when to put it back

### Object storage → `STORAGE_BACKEND=postgres`

Blobs live in a `blob_store` table. Presigned S3 URLs are replaced by an HMAC
over `(key, expiry)` served from `/api/v1/blobs`, so the contract is unchanged:
the API authorises the caller, *then* mints a link that expires and cannot be
forged.

**Put S3/R2 back when** storage passes a few GB, or page images are served at
any volume. Blobs in Postgres bloat the table, slow logical backups, and every
read is a database round trip instead of a CDN hit. Set `STORAGE_BACKEND=s3`
plus the `S3_*` variables — nothing else changes.

### Background worker → `WORKER_MODE=inline`

Render's free tier has no background workers, so documents are processed in an
asyncio task inside the API process. At a 30-page cap that is genuinely fine,
and because pipeline state lives in Postgres, a spin-down mid-run **resumes**
rather than restarts.

**Put the queue back when** throughput matters, so a slow document cannot
occupy a request worker's event loop. Set `WORKER_MODE=queue`, add `REDIS_URL`,
and run the same image with `arq docflow.worker.main.WorkerSettings`.

---

## Free-tier trade-offs, stated plainly

- **The API spins down after ~15 minutes idle.** The first request after that
  takes roughly 30–50 seconds. This is the single most noticeable thing about
  the free plan; a paid instance removes it.
- **Uploads are capped at 4 MB**, not the usual 10 MB, because the browser
  reaches the API through Vercel's proxy and its request-body limit is lower
  than ours. A university circular is ~1–2 MB.
- **Neon's free tier scales to zero**, adding a second or two to the first
  query after idle.

---

## Steps

### 1. Postgres (Neon)

Create a project at [neon.tech](https://neon.tech), Singapore region, and copy
the pooled connection string. Convert it for asyncpg:

```
postgresql://user:pass@host/db?sslmode=require
        ->
postgresql+asyncpg://user:pass@host/db
```

`?sslmode=require` must be **dropped** — asyncpg does not accept libpq's
`sslmode` parameter and will raise `TypeError: connect() got an unexpected
keyword argument 'sslmode'`. Neon requires TLS and asyncpg negotiates it by
default, so nothing is lost.

Neon ships `pgvector`; migration `0000_bootstrap` enables it.

### 2. API (Render)

Either import [`render.yaml`](../render.yaml) as a Blueprint, or:

```bash
render services create \
  --name docflow-api --type web_service --runtime docker \
  --repo https://github.com/A-K-M-Asifuzzaman/DocFlow-AI --branch main \
  --plan free --region singapore --health-check-path /health \
  --start-command 'sh -c "alembic upgrade head && uvicorn docflow.main:app --host 0.0.0.0 --port $PORT --workers 1"'
```

Required environment variables:

| Key | Value |
|---|---|
| `DATABASE_URL` | the Neon URL, `postgresql+asyncpg://…` |
| `DATABASE_ADMIN_URL` | same as above (Neon's user owns the schema) |
| `SECRET_KEY` | 48+ random bytes — the app refuses to boot in production with a weak one |
| `OPENAI_API_KEY` | your key |
| `STORAGE_BACKEND` | `postgres` |
| `WORKER_MODE` | `inline` |
| `ENVIRONMENT` | `production` |
| `APP_BASE_URL` | the Vercel URL — password-reset links are built from it |

Optional:

| Key | Value |
|---|---|
| `RESEND_API_KEY` | a [Resend](https://resend.com) key. Without it, reset emails are written to the Render log instead of being sent — the flow still completes, but only for someone who can read the log. |
| `EMAIL_FROM` | defaults to Resend's shared `onboarding@resend.dev`, which only delivers to the account owner. Set a verified domain to email anyone else. |

Model routing and cost caps are in [`render.yaml`](../render.yaml); the values
come from the benchmark in [EVALUATION.md](EVALUATION.md) §6.

The start command runs `alembic upgrade head` before the server, so migrations
apply on every deploy. Because migrations are expand-only, rolling back the
image is always safe.

### 3. Web (Vercel)

```bash
cd apps/web
vercel link --project docflow-ai
vercel env add API_INTERNAL_URL production   # https://<your-api>.onrender.com
vercel deploy --prod
```

Set the project's **Root Directory** to `apps/web`. Without it, `vercel deploy`
from this directory still works — but every git-push-triggered build runs from
the repo root, finds no `next` dependency, and fails with "No Next.js version
detected". The result is a project that looks connected to GitHub and silently
only ever ships what someone deploys by hand:

```bash
curl -X PATCH "https://api.vercel.com/v9/projects/<project-id>?teamId=<team-id>" \
  -H "Authorization: Bearer $VERCEL_TOKEN" -H "Content-Type: application/json" \
  -d '{"rootDirectory":"apps/web"}'
```

`next.config.ts` rewrites `/api/v1/*` to that URL, so the browser only ever
talks to the Vercel origin. That keeps the session **same-origin**: the refresh
cookie stays `SameSite=Lax`, and API calls carry no ambient cross-site
credential. Calling the API directly from the browser would require
`SameSite=None` cookies and CORS with credentials — strictly worse.

### 4. Seed the demo

```bash
render jobs create srv-<id> --command "python -m docflow.scripts.seed"
```

Four documents — three English, one Bangla routed through OCR — plus generated
tasks. Costs about $0.0035 in OpenAI calls.

---

## Cost

Measured, not estimated ([EVALUATION.md](EVALUATION.md) §6):

| | |
|---|---|
| Per short document | **$0.00088** |
| Seeding the demo (4 documents) | **$0.0035** |
| Hosting | **$0.00** |

Ceilings enforced in code, not just hoped for:

- `DAILY_BUDGET_PER_WORKSPACE_USD=0.25` — checked **before** each model call,
  roughly 280 documents/day
- `MAX_COST_PER_DOCUMENT_USD=0.10`
- `FREE_TIER_DOCUMENTS_PER_MONTH=20` per workspace
- `MAX_PAGE_COUNT=30`, `MAX_UPLOAD_BYTES=4194304`

Every call writes a `usage_records` row with its real cost, so
`GET /api/v1/usage` shows actual spend rather than a projection.

---

## Verifying a deployment

```bash
curl https://<api>/health/ready
# {"status":"ok","checks":{"database":true,"redis":true,"storage":true}}
```

`redis: true` in inline mode means "not a dependency", not "connected".

Then check the boot log for `startup.ocr_ready`. If you see
`startup.bengali_traineddata_missing`, Bangla documents will still process but
their citations will be unverifiable — Bengali OCR is the *primary* text path
for those pages, not a fallback ([EVALUATION.md](EVALUATION.md) §7).
