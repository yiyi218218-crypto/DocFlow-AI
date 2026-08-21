"""Verify a Firebase / Google ID token.

**No service account key is required, and none should be used here.**

A Firebase ID token is an ordinary RS256 JWT signed by Google. Verifying it
needs only Google's *public* keys, the project id as audience, and the issuer.
Pulling in `firebase-admin` — and with it a private key that grants full project
admin — merely to check a signature would be a large and needless increase in
blast radius: a leaked service account key is a compromised project, a leaked
public key is a public key.

Firebase is therefore an identity provider and nothing more. DocFlow still owns
the user record, the workspace, the session and the tenant model; the Google
token is exchanged exactly once for our own access and refresh tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from docflow.core.config import settings
from docflow.core.errors import Unauthenticated
from docflow.core.logging import get_logger

logger = get_logger(__name__)

JWKS_URL = (
    "https://www.googleapis.com/service_accounts/v1/jwk/"
    "securetoken@system.gserviceaccount.com"
)
KEY_CACHE_SECONDS = 3600

_jwk_client: PyJWKClient | None = None


def _client() -> PyJWKClient:
    """One cached JWKS client per process.

    PyJWKClient caches keys and refetches when it sees an unknown `kid`, which
    is exactly the behaviour Google's rotating signing keys need.
    """
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(JWKS_URL, cache_keys=True, lifespan=KEY_CACHE_SECONDS)
    return _jwk_client


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    subject: str
    email: str
    name: str | None = None
    picture: str | None = None


def verify_id_token(token: str) -> GoogleIdentity:
    """Verify a Firebase ID token and return the claims we use.

    Checks signature, expiry, issuer and audience, and refuses unverified email
    addresses — without that, anyone able to sign up at an IdP with someone
    else's unverified address could inherit their DocFlow workspace.
    """
    project_id = settings.firebase_project_id
    if not project_id:
        raise Unauthenticated("Google sign-in is not configured on this server.")

    try:
        signing_key = _client().get_signing_key_from_jwt(token)
        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=project_id,
            issuer=f"https://securetoken.google.com/{project_id}",
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthenticated("That sign-in has expired. Please try again.") from exc
    except (jwt.InvalidTokenError, PyJWKClientError) as exc:
        # PyJWKClientError covers an unknown `kid` — i.e. a token signed by
        # something that is not Google. That is a forged token, not a network
        # problem, and must not be reported as one: a misleading message here
        # sends you debugging connectivity while you are being attacked.
        logger.warning("google.token_invalid", reason=type(exc).__name__)
        raise Unauthenticated("Could not verify that Google sign-in.") from exc
    except Exception as exc:
        logger.error("google.jwks_unreachable", error=type(exc).__name__)
        raise Unauthenticated("Could not reach Google to verify sign-in.") from exc

    email = payload.get("email")
    if not email:
        raise Unauthenticated("That Google account has no email address attached.")

    if not payload.get("email_verified"):
        raise Unauthenticated(
            "Your Google email address is not verified. Verify it and try again."
        )

    return GoogleIdentity(
        subject=str(payload["sub"]),
        email=str(email).lower(),
        name=payload.get("name"),
        picture=payload.get("picture"),
    )
