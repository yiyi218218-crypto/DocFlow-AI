"""Google sign-in verification.

The whole security of this path is "the server does not trust the client's
claim about who it is". These tests exercise that boundary.

Note what is NOT here: a service account key. Firebase ID tokens are verified
against Google's PUBLIC keys, so the server needs no credential that could be
leaked. A test suite that needed one would be a design smell.
"""

from __future__ import annotations

import pytest

from docflow.core.config import settings
from docflow.core.errors import Unauthenticated
from docflow.services.google_auth import verify_id_token

# Header kid="fake": signed by something that is not Google.
FORGED = (
    "eyJhbGciOiJSUzI1NiIsImtpZCI6ImZha2UifQ"
    ".eyJzdWIiOiJhdHRhY2tlciIsImVtYWlsIjoiZGVtb0Bkb2NmbG93LmFpIiwiZW1haWxfdmVyaWZpZWQiOnRydWV9"
    ".bm90LWEtcmVhbC1zaWduYXR1cmU"
)


def test_forged_token_is_rejected() -> None:
    """A token signed by anyone but Google must not authenticate anybody.

    Without signature verification this exact payload claims to be
    demo@docflow.ai and would hand over that workspace.
    """
    with pytest.raises(Unauthenticated) as exc:
        verify_id_token(FORGED)
    # And it must say so honestly, rather than blaming the network.
    assert "verify" in str(exc.value).lower()


def test_garbage_is_rejected() -> None:
    with pytest.raises(Unauthenticated):
        verify_id_token("not-a-jwt-at-all")


def test_empty_token_is_rejected() -> None:
    with pytest.raises(Unauthenticated):
        verify_id_token("")


def test_disabled_when_project_id_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unconfigured server must refuse, not fall through to a weaker check."""
    monkeypatch.setattr(settings, "firebase_project_id", None)
    with pytest.raises(Unauthenticated) as exc:
        verify_id_token(FORGED)
    assert "not configured" in str(exc.value).lower()


def test_no_service_account_credential_is_used() -> None:
    """Guard the design decision itself.

    If someone later reaches for firebase-admin and a private key, this fails
    and they have to justify widening the blast radius from "a public key" to
    "full project admin".

    Checks imports and executable code rather than prose — the module docstring
    deliberately discusses service accounts in order to explain their absence.
    """
    import ast
    from pathlib import Path

    source = Path(__file__).parents[2] / "src/docflow/services/google_auth.py"
    tree = ast.parse(source.read_text())

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert "firebase_admin" not in imported, "verification must not need the Admin SDK"
    assert "google" not in imported, "verification must not need google-auth"

    # No credential-shaped literals anywhere in the executable source.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "BEGIN PRIVATE KEY" not in node.value
            assert "private_key" not in node.value
