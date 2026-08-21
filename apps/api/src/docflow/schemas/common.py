"""Shared API contract pieces.

Cursor pagination only. Offset pagination skips and duplicates rows under
concurrent inserts and degrades linearly with depth — and there is no endpoint
here where that trade is worth making.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Page[T](BaseModel):
    data: list[T]
    next_cursor: str | None = None
    has_more: bool = False


def encode_cursor(created_at: datetime, item_id: Any) -> str:
    """Keyset cursor over (created_at, id).

    The id breaks ties, so two rows created in the same microsecond still have a
    total order and neither is skipped.
    """
    payload = json.dumps({"c": created_at.isoformat(), "i": str(item_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        return datetime.fromisoformat(payload["c"]), payload["i"]
    except Exception:
        return None


class PaginationParams(BaseModel):
    cursor: str | None = None
    limit: int = Field(25, ge=1, le=100)


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    checks: dict[str, bool] | None = None
    demo_mode: bool = False
