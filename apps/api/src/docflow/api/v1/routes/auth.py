"""Auth endpoints.

The refresh token lives in an httpOnly cookie scoped to this router's refresh
path; the access token is returned in the body and held in browser memory. That
split is what makes API calls structurally CSRF-immune while keeping the refresh
credential out of JavaScript's reach.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from sqlalchemy import select

from docflow.api.v1.deps import CurrentUser, PlainSession, get_session
from docflow.core.config import settings
from docflow.core.errors import Unauthenticated
from docflow.core.permissions import permissions_for
from docflow.models.identity import User
from docflow.schemas.auth import (
    GoogleSignInIn,
    LoginIn,
    PasswordResetAccepted,
    PasswordResetIn,
    PasswordResetRequestIn,
    RegisterIn,
    SessionOut,
    TokenOut,
    UserOut,
    WorkspaceOut,
)
from docflow.services import auth_service
from docflow.services.email import render_password_reset, send_email
from docflow.services.google_auth import verify_id_token

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "docflow_refresh"
REFRESH_PATH = "/api/v1/auth"


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.refresh_token_ttl_days * 86400,
        httponly=True,
        secure=not settings.is_local,
        # Lax, not Strict: Strict breaks the OAuth callback return trip, and the
        # access token is not a cookie so there is no ambient credential to
        # protect on normal API calls anyway.
        samesite="lax",
        path=REFRESH_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)


@router.post("/register", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterIn,
    request: Request,
    response: Response,
    session: PlainSession,
) -> SessionOut:
    user, workspace = await auth_service.register(
        session,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        timezone=payload.timezone,
    )
    access, refresh = await auth_service.issue_tokens(
        session,
        user,
        workspace_id=workspace.id,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, refresh)
    response.headers["X-Access-Token"] = access

    from docflow.models.enums import WorkspaceRole

    return SessionOut(
        user=UserOut.model_validate(user),
        workspace=WorkspaceOut.model_validate(workspace),
        role=WorkspaceRole.OWNER,
        permissions=permissions_for(WorkspaceRole.OWNER),
    )


@router.post("/login", response_model=TokenOut)
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    session: PlainSession,
) -> TokenOut:
    user = await auth_service.authenticate(session, email=payload.email, password=payload.password)
    workspace, _role = await auth_service.default_workspace(session, user)
    access, refresh = await auth_service.issue_tokens(
        session,
        user,
        workspace_id=workspace.id,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, refresh)
    return TokenOut(
        access_token=access,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.post("/refresh", response_model=TokenOut)
async def refresh(
    request: Request,
    response: Response,
    session: PlainSession,
    docflow_refresh: Annotated[str | None, Cookie()] = None,
) -> TokenOut:
    if not docflow_refresh:
        raise Unauthenticated("Sign in to continue.")

    _user, access, new_refresh = await auth_service.rotate_refresh_token(
        session,
        docflow_refresh,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, new_refresh)
    return TokenOut(
        access_token=access,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: PlainSession,
    docflow_refresh: Annotated[str | None, Cookie()] = None,
) -> None:
    if docflow_refresh:
        await auth_service.revoke_session(session, docflow_refresh)
    _clear_refresh_cookie(response)


@router.post("/google", response_model=TokenOut)
async def google_sign_in(
    payload: GoogleSignInIn,
    request: Request,
    response: Response,
    session: PlainSession,
) -> TokenOut:
    """Exchange a verified Google ID token for a DocFlow session.

    Firebase is only an identity provider: the token is verified against
    Google's public keys and then discarded. Everything downstream — the
    session, the workspace, the tenant context — is ours, so the security model
    is unchanged whether someone signed in with a password or with Google.
    """
    identity = verify_id_token(payload.id_token)

    user, workspace = await auth_service.upsert_google_user(
        session,
        subject=identity.subject,
        email=identity.email,
        full_name=identity.name,
        picture=identity.picture,
    )

    access, refresh = await auth_service.issue_tokens(
        session,
        user,
        workspace_id=workspace.id,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, refresh)
    return TokenOut(
        access_token=access,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.get("/me", response_model=SessionOut)
async def me(
    user: CurrentUser,
    session: Annotated[object, Depends(get_session)],
) -> SessionOut:
    workspace, role = await auth_service.default_workspace(session, user)  # type: ignore[arg-type]
    return SessionOut(
        user=UserOut.model_validate(user),
        workspace=WorkspaceOut.model_validate(workspace),
        role=role,
        permissions=permissions_for(role),
    )


@router.post(
    "/forgot-password",
    response_model=PasswordResetAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def forgot_password(
    payload: PasswordResetRequestIn,
    session: PlainSession,
) -> PasswordResetAccepted:
    """Send a reset link. Always 202, whether or not the account exists.

    Any other behaviour — a different status, a different message, even a
    noticeably different response time — turns this into an account-enumeration
    oracle (T14).
    """
    reset_url = await auth_service.begin_password_reset(session, email=payload.email)

    if reset_url is not None:
        user = await session.scalar(select(User).where(User.email == payload.email))
        subject, text, html = render_password_reset(
            reset_url=reset_url, full_name=user.full_name if user else None
        )
        # When no provider is configured this writes the link to the log, which
        # is how a local or free-tier deployment completes the flow.
        await send_email(to=payload.email, subject=subject, text=text, html=html)

    # One response, byte-identical either way.
    #
    # An earlier version returned the link in the body when no email provider
    # was configured, to make local testing easy. That is an account-enumeration
    # oracle: present for real addresses, absent for the rest, so anyone could
    # test which emails are registered — and in any environment sharing the
    # build, reset any account they could name. The convenience is not worth it;
    # the link goes to the log instead.
    return PasswordResetAccepted(
        detail="If that address has an account, a reset link is on its way."
    )


@router.post("/reset-password", response_model=TokenOut)
async def reset_password(
    payload: PasswordResetIn,
    request: Request,
    response: Response,
    session: PlainSession,
) -> TokenOut:
    """Set a new password and sign the user straight in.

    Signing them in is deliberate: the alternative is bouncing someone who has
    just proved control of the address back to a login form.
    """
    user = await auth_service.complete_password_reset(
        session, token=payload.token, new_password=payload.password
    )
    workspace, _role = await auth_service.default_workspace(session, user)

    access, refresh = await auth_service.issue_tokens(
        session,
        user,
        workspace_id=workspace.id,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, refresh)
    return TokenOut(access_token=access, expires_in=settings.access_token_ttl_minutes * 60)
