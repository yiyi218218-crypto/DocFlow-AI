"""Auth and workspace contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from docflow.models.enums import WorkspaceRole
from docflow.schemas.common import APIModel

MIN_PASSWORD_LENGTH = 10


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=200)
    full_name: str | None = Field(None, max_length=200)
    timezone: str = "Asia/Dhaka"

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        """A length floor plus a variety check.

        Deliberately not a symbol-and-uppercase ruleset: those push users toward
        'Password1!' and measurably reduce entropy. Length is what matters.
        """
        if v.isdigit() or v.isalpha():
            raise ValueError("Use a mix of letters and numbers, or a longer passphrase.")
        if v.lower() in {"password12", "1234567890", "qwertyuiop"}:
            raise ValueError("That password is too common.")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(..., max_length=200)


class TokenOut(BaseModel):
    """The refresh token is NOT in this body.

    It is set as an httpOnly cookie scoped to the refresh endpoint. The access
    token stays in browser memory, so API calls carry no ambient credential and
    are structurally CSRF-immune.
    """

    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token_type literal, not a secret
    expires_in: int


class UserOut(APIModel):
    id: uuid.UUID
    email: str
    full_name: str | None = None
    avatar_url: str | None = None
    timezone: str
    locale: str
    email_verified_at: datetime | None = None
    created_at: datetime

    @property
    def is_verified(self) -> bool:
        return self.email_verified_at is not None


class WorkspaceOut(APIModel):
    id: uuid.UUID
    name: str
    slug: str
    timezone: str
    plan: str
    created_at: datetime


class MembershipOut(APIModel):
    id: uuid.UUID
    role: WorkspaceRole
    joined_at: datetime
    user: UserOut


class SessionOut(BaseModel):
    """Everything the client needs after login, in one response."""

    user: UserOut
    workspace: WorkspaceOut
    role: WorkspaceRole
    permissions: list[str]


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetIn(BaseModel):
    token: str
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=200)


class WorkspaceCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    timezone: str = "Asia/Dhaka"
