"""Prompt loading and versioning.

Prompts are files, not string literals: diffable, reviewable in a PR, and
recorded on every `usage_records` row and every `document_stages` idempotency
key. That makes "which prompt produced this fact?" answerable, and it makes
changing a prompt a data-lineage event — the affected stage re-runs and nothing
else does.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


class PromptNotFound(Exception):
    pass


@lru_cache(maxsize=64)
def load_prompt(relative_path: str) -> str:
    """Load a prompt by path relative to the prompts directory, e.g.
    'extraction/v1_core.md'."""
    path = PROMPTS_DIR / relative_path
    if not path.is_file():
        raise PromptNotFound(f"No prompt at {relative_path}")
    return path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=64)
def prompt_version(relative_path: str) -> str:
    """A stable identifier combining the filename and a content hash.

    The hash matters: editing a prompt without renaming it must still invalidate
    the cached stage, or a backfill silently reuses stale extractions.
    """
    content = load_prompt(relative_path)
    digest = hashlib.sha256(content.encode()).hexdigest()[:8]
    stem = Path(relative_path).stem
    return f"{stem}@{digest}"


def compose_system_prompt(*relative_paths: str) -> tuple[str, str]:
    """Concatenate prompt files into one system prompt.

    Returns (text, version). Ordered core-first, then type-specific overrides, so
    a domain file can add rules without restating the shared ones.
    """
    parts = [load_prompt(p) for p in relative_paths]
    versions = "+".join(prompt_version(p) for p in relative_paths)
    return "\n\n---\n\n".join(parts), versions
