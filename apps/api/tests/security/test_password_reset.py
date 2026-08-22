"""Password reset.

The flow has three security properties worth testing explicitly, because each
one is easy to get subtly wrong and none of them is visible from the happy path.
"""

from __future__ import annotations

import uuid

import pytest

from docflow.core.errors import Unauthenticated
from docflow.core.security import (
    create_password_reset_token,
    hash_password,
    verify_password_reset_token,
)

USER = uuid.uuid4()


def test_token_round_trips() -> None:
    pw = hash_password("original-password-9")
    token = create_password_reset_token(USER, pw)
    assert verify_password_reset_token(token, pw) == USER


def test_token_is_single_use() -> None:
    """The token is bound to the password hash it was minted against, so
    changing the password kills it — no table, no cleanup job, no window where
    a spent link still works."""
    old = hash_password("original-password-9")
    token = create_password_reset_token(USER, old)

    new = hash_password("a-brand-new-password-9")
    with pytest.raises(Unauthenticated) as exc:
        verify_password_reset_token(token, new)
    assert "already been used" in str(exc.value)


def test_every_outstanding_link_dies_together() -> None:
    """Two links requested before a reset must BOTH stop working after it.

    Otherwise requesting a second link silently extends the life of the first.
    """
    old = hash_password("original-password-9")
    first = create_password_reset_token(USER, old)
    second = create_password_reset_token(USER, old)

    changed = hash_password("changed-password-9")
    for token in (first, second):
        with pytest.raises(Unauthenticated):
            verify_password_reset_token(token, changed)


def test_google_only_account_can_set_a_password() -> None:
    """A user who signed up with Google has no hash yet; they must still be
    able to add a password rather than being locked out of that path."""
    token = create_password_reset_token(USER, None)
    assert verify_password_reset_token(token, None) == USER


def test_forged_token_is_rejected() -> None:
    with pytest.raises(Unauthenticated):
        verify_password_reset_token("not.a.token", hash_password("whatever-9"))


def test_access_token_cannot_be_used_to_reset() -> None:
    """Token types must not be interchangeable: a stolen access token must not
    be redeemable for a password change."""
    from docflow.core.security import create_access_token

    access = create_access_token(USER)
    with pytest.raises(Unauthenticated):
        verify_password_reset_token(access, hash_password("whatever-9"))


async def test_log_backend_writes_the_whole_link(monkeypatch) -> None:
    """With no email provider the log *is* the delivery channel.

    This caught a real bug: the log line truncated the body at 400 characters,
    which lands in the middle of the ~250-character token. The URL looked
    complete in the log and failed on use.
    """
    from docflow.services import email as email_module

    recorded: dict[str, object] = {}

    class Recorder:
        def info(self, event: str, **kw: object) -> None:
            recorded.update(kw)

        def error(self, event: str, **kw: object) -> None:  # pragma: no cover
            raise AssertionError(f"unexpected error log: {event}")

    monkeypatch.setattr(email_module.settings, "resend_api_key", None)
    monkeypatch.setattr(email_module, "logger", Recorder())

    token = create_password_reset_token(USER, hash_password("original-9"))
    reset_url = f"https://docflow.example/reset-password?token={token}"
    subject, text, _html = email_module.render_password_reset(
        reset_url=reset_url, full_name="Nusrat Jahan"
    )

    result = await email_module.send_email(to="nusrat@example.com", subject=subject, text=text)

    assert result.delivered is False and result.backend == "log"
    assert reset_url in str(recorded["body"]), "the logged link must be usable as-is"
