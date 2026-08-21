# One image, two entrypoints.
#
# The API and the worker share a package, so they must share an image — building
# them separately is how the worker ends up on an older pymupdf and produces
# subtly different coordinates than the API expects. Only the CMD differs.

FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# tesseract-ocr-ben is the Bangla traineddata. Without it Tesseract fails the
# whole page rather than falling back, so a partial capability becomes none.
# libreoffice is only needed for DOCX; documents/validation.py degrades to text
# extraction when it is absent, so it can be dropped to save ~450 MB.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-ben \
        libreoffice-writer-nogui \
        fonts-noto-core \
        fonts-noto-ui-core \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first: this layer is cached unless the lockfile changes.
COPY apps/api/pyproject.toml apps/api/uv.lock* ./
RUN uv sync --no-dev --no-install-project

COPY apps/api/src ./src
COPY apps/api/alembic.ini ./
RUN uv sync --no-dev

# Never run as root.
RUN useradd --create-home --uid 10001 docflow && chown -R docflow:docflow /app
USER docflow

ENV PATH="/app/.venv/bin:$PATH"

# ---------------------------------------------------------------- api --------
FROM base AS api
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1
CMD ["uvicorn", "docflow.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

# ------------------------------------------------------------- worker --------
FROM base AS worker
# ARQ drains the in-flight stage on SIGTERM before exiting, so a rolling deploy
# does not abandon a half-processed document.
CMD ["arq", "docflow.worker.main.WorkerSettings"]
