"""Authentication.

Refresh tokens rotate on every use and are tracked by family. Presenting a token
that has already been rotated means it was captured, so the entire family is
revoked — that is how token theft is detected rather than merely made
inconvenient. docs/50-security-ops.md §16.3 T13.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.core.config import settings
from docflow.core.errors import (
    EmailAlreadyRegistered,
    InvalidCredentials,
    TokenReuseDetected,
    Unauthenticated,
)
from docflow.core.logging import get_logger
from docflow.core.security import (
    create_access_token,
    create_password_reset_token,
    create_refresh_token,
    decode_token,
    dummy_verify,
    hash_password,
    hash_secret,
    needs_rehash,
    verify_password,
    verify_password_reset_token,
)
from docflow.models.enums import WorkspaceRole
from docflow.models.identity import Membership, RefreshToken, User, Workspace

logger = get_logger(__name__)

MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15


async def register(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str | None = None,
    timezone: str = "Asia/Dhaka",
) -> tuple[User, Workspace]:
    """Create a user and their first workspace.

    A workspace is created immediately even though the MVP UI only ever shows
    one: the tenant model is workspace-scoped from day one, so adding a second
    member later is a UI change rather than a migration.
    """
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        # Enumeration is limited by returning the same generic conflict the UI
        # shows for any duplicate; the real mitigation is that password reset
        # and registration emails do not differ observably (T14).
        raise EmailAlreadyRegistered()

    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        timezone=timezone,
    )
    session.add(user)
    await session.flush()

    workspace = Workspace(
        name=_default_workspace_name(full_name, email),
        slug=await _unique_slug(session, full_name or email.split("@")[0]),
        owner_id=user.id,
        timezone=timezone,
    )
    session.add(workspace)
    await session.flush()

    session.add(Membership(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OWNER))
    await session.flush()

    logger.info("auth.registered", user_id=str(user.id), workspace_id=str(workspace.id))
    return user, workspace


async def upsert_google_user(
    session: AsyncSession,
    *,
    subject: str,
    email: str,
    full_name: str | None,
    picture: str | None,
) -> tuple[User, Workspace]:
    """Find or create the DocFlow user behind a verified Google identity.

    Matching is by oauth subject first, then by email. The email fallback is
    what lets someone who signed up with a password later use Google without
    ending up with two accounts — and it is only safe because the caller has
    already required a *verified* address.
    """
    user = await session.scalar(
        select(User).where(User.oauth_provider == "google", User.oauth_subject == subject)
    )

    if user is None:
        user = await session.scalar(select(User).where(User.email == email))
        if user is not None:
            # Existing password account: attach the identity rather than
            # creating a duplicate.
            user.oauth_provider = "google"
            user.oauth_subject = subject
            logger.info("auth.google_linked", user_id=str(user.id))

    if user is None:
        user = User(
            email=email,
            password_hash=None,  # Google-only account until they set one
            full_name=full_name,
            avatar_url=picture,
            oauth_provider="google",
            oauth_subject=subject,
            email_verified_at=datetime.now(UTC),
        )
        session.add(user)
        await session.flush()

        workspace = Workspace(
            name=_default_workspace_name(full_name, email),
            slug=await _unique_slug(session, full_name or email.split("@")[0]),
            owner_id=user.id,
        )
        session.add(workspace)
        await session.flush()
        session.add(
            Membership(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OWNER)
        )
        await session.flush()
        logger.info("auth.google_registered", user_id=str(user.id))
        return user, workspace

    # Keep the profile fresh, but never overwrite a name the user set here.
    if picture:
        user.avatar_url = picture
    if full_name and not user.full_name:
        user.full_name = full_name
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
    user.last_login_at = datetime.now(UTC)
    user.failed_login_count = 0
    user.locked_until = None

    workspace, _role = await default_workspace(session, user)
    return user, workspace


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    user = await session.scalar(select(User).where(User.email == email))

    if user is None or user.password_hash is None:
        # Burn equivalent CPU so response timing does not reveal whether the
        # account exists (T14).
        dummy_verify()
        raise InvalidCredentials()

    if user.is_locked:
        raise InvalidCredentials("Too many failed attempts. Try again in a few minutes.")

    if not verify_password(password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.locked_until = datetime.now(UTC) + timedelta(minutes=LOCKOUT_MINUTES)
            logger.warning("auth.account_locked", user_id=str(user.id))
        raise InvalidCredentials()

    # Transparent upgrade if the cost parameters have since been raised.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = datetime.now(UTC)
    return user


async def issue_tokens(
    session: AsyncSession,
    user: User,
    *,
    workspace_id: uuid.UUID | None = None,
    family_id: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, str]:
    """Return (access_token, refresh_token). The refresh token is stored hashed."""
    access = create_access_token(user.id, workspace_id)
    refresh, family = create_refresh_token(user.id, family_id)

    session.add(
        RefreshToken(
            user_id=user.id,
            family_id=family,
            token_hash=hash_secret(refresh),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
            user_agent=(user_agent or "")[:300] or None,
            ip_address=(ip_address or "")[:64] or None,
        )
    )
    return access, refresh


async def rotate_refresh_token(
    session: AsyncSession,
    raw_token: str,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[User, str, str]:
    """Exchange a refresh token for a new pair, detecting reuse."""
    payload = decode_token(raw_token, "refresh")
    token_hash = hash_secret(raw_token)

    stored = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if stored is None:
        raise Unauthenticated("Your session has expired. Please sign in again.")

    if not stored.is_active:
        # This exact token was already rotated or revoked, yet someone still has
        # it. Assume capture and kill every descendant of that login.
        await _revoke_family(session, stored.family_id, reason="reuse_detected")
        logger.warning(
            "auth.refresh_reuse_detected",
            user_id=str(stored.user_id),
            family=stored.family_id,
        )
        raise TokenReuseDetected(
            "This session was ended for security reasons. Please sign in again."
        )

    if stored.expires_at < datetime.now(UTC):
        raise Unauthenticated("Your session has expired. Please sign in again.")

    user = await session.scalar(select(User).where(User.id == stored.user_id))
    if user is None:
        raise Unauthenticated("Your session has expired. Please sign in again.")

    stored.rotated_at = datetime.now(UTC)
    workspace_id = payload.get("ws")

    access, refresh = await issue_tokens(
        session,
        user,
        workspace_id=uuid.UUID(workspace_id) if workspace_id else None,
        family_id=stored.family_id,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    return user, access, refresh


async def revoke_session(session: AsyncSession, raw_token: str) -> None:
    """Log out: revoke the whole family, not just this token."""
    stored = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_secret(raw_token))
    )
    if stored is not None:
        await _revoke_family(session, stored.family_id, reason="logout")


async def _revoke_family(session: AsyncSession, family_id: str, *, reason: str) -> None:
    tokens = (
        (
            await session.execute(
                select(RefreshToken).where(
                    RefreshToken.family_id == family_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    for token in tokens:
        token.revoked_at = now
        token.revoked_reason = reason


async def default_workspace(session: AsyncSession, user: User) -> tuple[Workspace, WorkspaceRole]:
    row = (
        await session.execute(
            select(Workspace, Membership)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(Membership.user_id == user.id, Workspace.deleted_at.is_(None))
            .order_by(Membership.joined_at.asc())
            .limit(1)
        )
    ).first()
    if row is None:
        raise Unauthenticated("No workspace is available for this account.")
    workspace, membership = row
    return workspace, membership.role


def _default_workspace_name(full_name: str | None, email: str) -> str:
    base = (full_name or email.split("@")[0]).strip()
    first = base.split()[0] if base else "My"
    return f"{first}'s Workspace"


async def _unique_slug(session: AsyncSession, seed: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", seed.lower()).strip("-")[:40] or "workspace"
    slug = base
    for attempt in range(50):
        exists = await session.scalar(select(Workspace.id).where(Workspace.slug == slug))
        if exists is None:
            return slug
        slug = f"{base}-{uuid.uuid4().hex[:6]}" if attempt else f"{base}-2"
    return f"{base}-{uuid.uuid4().hex[:10]}"


# --- password reset -------------------------------------------------------


async def begin_password_reset(session: AsyncSession, *, email: str) -> str | None:
    """Create a reset link for `email`, or return None if there is no account.

    The caller MUST respond identically either way. Whether an address is
    registered is exactly what an enumeration probe is looking for (T14).
    """
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        return None

    token = create_password_reset_token(user.id, user.password_hash)
    return f"{settings.app_base_url.rstrip('/')}/reset-password?token={token}"


async def complete_password_reset(session: AsyncSession, *, token: str, new_password: str) -> User:
    """Set a new password and end every existing session."""
    payload = decode_token(token, "password_reset")
    user = await session.scalar(select(User).where(User.id == uuid.UUID(payload["sub"])))
    if user is None:
        raise Unauthenticated("This reset link is no longer valid.")

    # Re-verify against the CURRENT hash: this is what makes the link single-use.
    verify_password_reset_token(token, user.password_hash)

    user.password_hash = hash_password(new_password)
    user.failed_login_count = 0
    user.locked_until = None
    if user.email_verified_at is None:
        # Receiving the email proves control of the address.
        user.email_verified_at = datetime.now(UTC)

    # A reset is the remedy for a compromised account, so it has to log out
    # everyone else — otherwise a thief keeps their session and the reset
    # accomplishes nothing.
    revoked = (
        (
            await session.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user.id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    for stored in revoked:
        stored.revoked_at = now
        stored.revoked_reason = "password_reset"

    logger.info("auth.password_reset", user_id=str(user.id), sessions_revoked=len(revoked))
    return user
