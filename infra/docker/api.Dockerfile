# One image, two roles.
#
# The API and the worker share a package, so they must share an image — building
# them separately is how the worker ends up on an older pymupdf and produces
# different coordinates than the API expects. The default command runs the API;
# the worker runs the same image with `arq docflow.worker.main.WorkerSettings`.
#
# On a single-service deployment neither is needed: WORKER_MODE=inline processes
# documents inside the API process. See render.yaml.

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PORT=8000

# tesseract-ocr-ben is not optional. It is the *primary* text path for Bengali
# pages, not a fallback: a Bengali PDF's own text layer is measurably worse than
# OCR of the same page (docs/EVALUATION.md §7). Without this traineddata the
# wedge silently produces unverifiable citations.
#
# LibreOffice is deliberately NOT installed — it is a ~450 MB layer used only
# for DOCX, and documents/validation.py falls back to OOXML text extraction
# without it. Layout fidelity is lost; deadlines and requirements survive, which
# is what the product is for. Add `libreoffice-writer-nogui` if you need it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-ben \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first: cached unless the lockfile changes.
COPY apps/api/pyproject.toml apps/api/uv.lock* ./
RUN uv sync --no-dev --no-install-project

COPY apps/api/src ./src
COPY apps/api/alembic.ini ./
RUN uv sync --no-dev

RUN useradd --create-home --uid 10001 docflow && chown -R docflow:docflow /app
USER docflow

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

# Shell form so $PORT expands — hosting platforms assign it at runtime.
CMD uvicorn docflow.main:app --host 0.0.0.0 --port ${PORT} --workers 1
