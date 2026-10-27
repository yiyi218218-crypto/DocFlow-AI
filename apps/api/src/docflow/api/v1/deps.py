"""FastAPI dependencies: authentication, tenant context, authorization.

The important property here is that the workspace comes from the *session*, not
from anything the client can set freely. `X-Workspace-Id` is only ever used to
select among workspaces the user is already a member of — membership is verified
before the tenant GUC is bound, so a forged header selects nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.core.database import SessionFactory, set_tenant_context
from docflow.core.errors import Forbidden, NotFound, Unauthenticated
from docflow.core.logging import user_id_var, workspace_id_var
from docflow.core.permissions import Permission, can
from docflow.core.security import decode_token
from docflow.models.enums import WorkspaceRole
from docflow.models.identity import Membership, User, Workspace


@dataclass(slots=True)
class Principal:
    """Who is asking, in which workspace, with what role."""

    user: User
    workspace: Workspace
    role: WorkspaceRole

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def workspace_id(self) -> uuid.UUID:
        return self.workspace.id

    def require(self, permission: Permission) -> None:
        if not can(self.role, permission):
            raise Forbidden(f"Your role ({self.role.value}) cannot perform this action.")


async def get_session() -> AsyncIterator[AsyncSession]:
    """A plain session with no tenant context.

    Only for auth endpoints, which legitimately operate across workspaces (a
    login does not yet know which workspace it is for).
    """
    async with SessionFactory() as session, session.begin():
        yield session


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthenticated("Sign in to continue.")
    return authorization[7:].strip()


async def get_current_user(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> User:
    payload = decode_token(_bearer_token(authorization), "access")
    user = await session.scalar(select(User).where(User.id == uuid.UUID(payload["sub"])))
    if user is None:
        raise Unauthenticated("Sign in to continue.")

    request.state.user_id = str(user.id)
    user_id_var.set(str(user.id))
    return user


async def get_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_workspace_id: Annotated[str | None, Header()] = None,
) -> AsyncIterator[Principal]:
    """Authenticate, resolve the workspace, and bind the RLS tenant context.

    Yields inside an open transaction with the GUC set, so every query in the
    request is covered by the policy — and a handler that forgets its WHERE
    clause returns nothing rather than another tenant's rows.
    """
    payload = decode_token(_bearer_token(authorization), "access")
    user_id = uuid.UUID(payload["sub"])

    async with SessionFactory() as session, session.begin():
        user = await session.scalar(select(User).where(User.id == user_id))
        if user is None:
            raise Unauthenticated("Sign in to continue.")

        query = (
            select(Membership, Workspace)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(
                Membership.user_id == user_id,
                Workspace.deleted_at.is_(None),
            )
        )
        # The header only narrows an already-authorised set; it can never widen it.
        if x_workspace_id:
            try:
                query = query.where(Membership.workspace_id == uuid.UUID(x_workspace_id))
            except ValueError as exc:
                raise NotFound("Workspace not found.") from exc
        else:
            query = query.order_by(Membership.joined_at.asc())

        row = (await session.execute(query.limit(1))).first()
        if row is None:
            # 404 rather than 403: a 403 would confirm the workspace exists.
            raise NotFound("Workspace not found.")

        membership, workspace = row
        await set_tenant_context(session, workspace.id, user.id)

        request.state.user_id = str(user.id)
        request.state.workspace_id = str(workspace.id)
        user_id_var.set(str(user.id))
        workspace_id_var.set(str(workspace.id))

        principal = Principal(user=user, workspace=workspace, role=membership.role)
        principal_session.set(session)
        try:
            yield principal
        finally:
            principal_session.set(None)


# The tenant-bound session for the current request, so handlers do not need to
# thread it through every signature alongside the principal.
from contextvars import ContextVar  # noqa: E402

principal_session: ContextVar[AsyncSession | None] = ContextVar("principal_session", default=None)


async def get_tenant_session(
    principal: Annotated[Principal, Depends(get_principal)],
) -> AsyncSession:
    session = principal_session.get()
    if session is None:  # pragma: no cover - dependency ordering guarantees this
        raise RuntimeError("tenant session requested outside a principal-scoped request")
    _ = principal
    return session


def require(permission: Permission):
    """Route dependency enforcing one permission.

    Usage: `dependencies=[Depends(require(Permission.DOCUMENT_UPLOAD))]`
    """

    async def _check(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        principal.require(permission)
        return principal

    return _check


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
PlainSession = Annotated[AsyncSession, Depends(get_session)]
