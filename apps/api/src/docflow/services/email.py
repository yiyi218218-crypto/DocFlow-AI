"""Transactional email.

Two backends behind one interface:

  * **resend** — used when RESEND_API_KEY is set.
  * **log** — the default. Renders the message and writes it to the log instead
    of sending it.

The log backend is not a stub that silently drops mail. It exists so the
password-reset flow is fully exercisable — locally, in CI, and on a free-tier
deployment with no email provider — and so the absence of delivery is *visible*
in the logs rather than looking like success. `delivered` on the result says
which happened, and the caller must not leak that to the client: whether an
email was sent is exactly what an account-enumeration probe wants to learn.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from docflow.core.config import settings
from docflow.core.logging import get_logger

logger = get_logger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"


@dataclass(frozen=True, slots=True)
class EmailResult:
    delivered: bool
    backend: str
    detail: str | None = None


async def send_email(*, to: str, subject: str, text: str, html: str | None = None) -> EmailResult:
    if not settings.resend_api_key:
        # Deliberately logs the body: on a deployment with no provider this is
        # the only way to complete a reset, and it beats a flow that appears to
        # work and quietly goes nowhere.
        # The whole body, not a preview. A reset token is ~250 characters and
        # sits at the end of the link, so any truncation produces a URL that
        # looks right and fails on use — which is worse than logging nothing.
        logger.info("email.not_configured", to=to, subject=subject, body=text)
        return EmailResult(delivered=False, backend="log", detail="No email provider configured.")

    payload = {
        "from": settings.email_from,
        "to": [to],
        "subject": subject,
        "text": text,
    }
    if html:
        payload["html"] = html

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                RESEND_ENDPOINT,
                json=payload,
                headers={"Authorization": f"Bearer {settings.resend_api_key.get_secret_value()}"},
            )
        if response.status_code >= 400:
            logger.error("email.send_failed", status=response.status_code, to=to)
            return EmailResult(delivered=False, backend="resend", detail="Provider rejected it.")
    except httpx.HTTPError as exc:
        logger.error("email.transport_error", error=type(exc).__name__, to=to)
        return EmailResult(delivered=False, backend="resend", detail="Provider unreachable.")

    logger.info("email.sent", to=to, subject=subject)
    return EmailResult(delivered=True, backend="resend")


def render_password_reset(*, reset_url: str, full_name: str | None) -> tuple[str, str, str]:
    """Return (subject, text, html) for a reset email."""
    greeting = f"Hi {full_name.split()[0]}," if full_name else "Hi,"
    subject = "Reset your DocFlow password"

    text = (
        f"{greeting}\n\n"
        "Someone asked to reset the password on your DocFlow account. "
        "Open this link within the next hour to choose a new one:\n\n"
        f"{reset_url}\n\n"
        "If that wasn't you, ignore this email — your password stays as it is, "
        "and the link cannot be used without opening it.\n\n"
        "DocFlow"
    )

    html = f"""<!doctype html>
<div style="font-family:ui-sans-serif,system-ui,sans-serif;font-size:15px;
            line-height:1.55;color:#26221e;max-width:34rem">
  <p>{greeting}</p>
  <p>Someone asked to reset the password on your DocFlow account.
     Open this link within the next hour to choose a new one.</p>
  <p style="margin:24px 0">
    <a href="{reset_url}" style="background:#a8551f;color:#fff;padding:11px 18px;
       border-radius:6px;text-decoration:none;font-weight:500;display:inline-block">
      Choose a new password
    </a>
  </p>
  <p style="color:#6b635b;font-size:13px">
    If that wasn't you, ignore this email — your password stays as it is.
  </p>
</div>"""

    return subject, text, html
