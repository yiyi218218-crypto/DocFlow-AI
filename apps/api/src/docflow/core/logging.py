"""Structured logging.

Two rules that make logs queryable instead of merely readable:
  1. `event` is a stable dotted name, never an interpolated sentence.
  2. Nothing sensitive is ever logged — a redaction processor is the backstop,
     not the primary control.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
workspace_id_var: ContextVar[str | None] = ContextVar("workspace_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "set-cookie",
        "api_key",
        "secret",
        "secret_key",
        "presigned_url",
        "upload_url",
        "prompt",
        "document_text",
        "content",
        "source_quote",
    }
)


def _redact(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = "[redacted]"
    return event_dict


def _bind_context(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    if (rid := request_id_var.get()) is not None:
        event_dict.setdefault("request_id", rid)
    if (wid := workspace_id_var.get()) is not None:
        event_dict.setdefault("workspace_id", wid)
    if (uid := user_id_var.get()) is not None:
        event_dict.setdefault("user_id", uid)
    return event_dict


def configure_logging(
    *, level: str = "INFO", json_output: bool = True, service: str = "api"
) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    for noisy in ("uvicorn.access", "botocore", "boto3", "urllib3", "httpx", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _bind_context,
        _redact,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)


def get_logger(name: str = "docflow") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
