"""Password hashing, JWTs, and refresh-token rotation with reuse detection.

Access tokens are short-lived and held in browser memory (never localStorage), so
API calls carry no ambient credential and are structurally CSRF-immune.
Refresh tokens live in an httpOnly cookie scoped to the refresh endpoint, and are
rotated on every use: replaying one revokes the entire family, which is how token
theft is detected. docs/50-security-ops.md §16.3 T10/T13.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from docflow.core.config import settings
from docflow.core.errors import TokenExpired, Unauthenticated

_hasher = PasswordHasher(
    time_cost=settings.argon2_time_cost,
    memory_cost=settings.argon2_memory_cost,
    parallelism=settings.argon2_parallelism,
)

ALGORITHM = "HS256"
TokenType = Literal["access", "refresh", "email_verify", "password_reset", "ics_feed"]


# --- passwords ------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def dummy_verify() -> None:
    """Burn an equivalent amount of CPU on a non-existent user.

    Without this, login timing reveals whether an email is registered (T14).
    """
    _hasher.hash("timing-equalisation-placeholder")


# --- JWT ------------------------------------------------------------------


def _key() -> str:
    return settings.secret_key.get_secret_value()


def create_token(
    subject: str | uuid.UUID,
    token_type: TokenType,
    *,
    expires_in: timedelta | None = None,
    **claims: Any,
) -> str:
    now = datetime.now(UTC)
    default_ttl = {
        "access": timedelta(minutes=settings.access_token_ttl_minutes),
        "refresh": timedelta(days=settings.refresh_token_ttl_days),
        "email_verify": timedelta(hours=24),
        "password_reset": timedelta(hours=1),
        "ics_feed": timedelta(days=3650),
    }[token_type]

    payload: dict[str, Any] = {
        "sub": str(subject),
        "typ": token_type,
        "iat": now,
        "exp": now + (expires_in or default_ttl),
        "jti": secrets.token_urlsafe(16),
        **claims,
    }
    return jwt.encode(payload, _key(), algorithm=ALGORITHM)


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = jwt.decode(token, _key(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired("Your session has expired. Please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthenticated("Invalid authentication token.") from exc

    if payload.get("typ") != expected_type:
        raise Unauthenticated("Invalid authentication token.")
    return payload


def create_access_token(user_id: uuid.UUID, workspace_id: uuid.UUID | None = None) -> str:
    return create_token(user_id, "access", ws=str(workspace_id) if workspace_id else None)


def create_refresh_token(user_id: uuid.UUID, family_id: str | None = None) -> tuple[str, str]:
    """Return (token, family_id). All tokens descended from one login share a family."""
    family = family_id or secrets.token_urlsafe(16)
    return create_token(user_id, "refresh", fam=family), family


# --- opaque secrets -------------------------------------------------------


def generate_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_secret(value: str) -> str:
    """Refresh tokens and API keys are stored hashed — a DB dump is not a key dump."""
    return hashlib.sha256(value.encode()).hexdigest()


def constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# --- password reset -------------------------------------------------------


def password_fingerprint(password_hash: str | None) -> str:
    """A short digest of the credential a reset token is bound to.

    Binding the token to the CURRENT password hash makes it single-use for
    free: the moment the password changes the fingerprint no longer matches and
    every outstanding link for that account is dead. No table, no cleanup job,
    no window where a used link still works.

    A Google-only account has no hash yet; it gets a stable placeholder so such
    a user can still set a password.
    """
    return hashlib.sha256((password_hash or "no-password").encode()).hexdigest()[:16]


def create_password_reset_token(user_id: uuid.UUID, password_hash: str | None) -> str:
    return create_token(user_id, "password_reset", pw=password_fingerprint(password_hash))


def verify_password_reset_token(token: str, password_hash: str | None) -> uuid.UUID:
    """Return the user id, or raise if the link is spent, forged or expired."""
    payload = decode_token(token, "password_reset")
    if payload.get("pw") != password_fingerprint(password_hash):
        raise Unauthenticated("This reset link has already been used.")
    return uuid.UUID(payload["sub"])
