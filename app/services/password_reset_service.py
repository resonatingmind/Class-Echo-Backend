"""Self-service password reset links.

Same token lifecycle as invites (raw token emailed, only its hash stored), but
short-lived: a reset link grants a password change on an account that is
already in use, so it should not sit valid in an inbox for days.
"""

import html
import logging
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_token_pair
from app.models.auth_token import AuthToken
from app.services import invite_service
from app.services.email_service import deliver_email

logger = logging.getLogger(__name__)

RESET_TTL_MINUTES = 60
PURPOSE = "password_reset"


async def burn_reset_tokens(db: AsyncSession, user_type: str, user_id: int) -> None:
    """Invalidate every outstanding reset link for one account."""
    await db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_type == user_type,
            AuthToken.user_id == user_id,
            AuthToken.purpose == PURPOSE,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=datetime.utcnow())
    )


async def issue_reset_token(db: AsyncSession, user_type: str, user_id: int) -> str:
    """Mint a reset token and return the raw (emailable) half.

    Earlier unused links are burned first so only the most recent email works;
    otherwise every repeated "forgot password" click leaves another live link.
    """
    await burn_reset_tokens(db, user_type, user_id)
    raw_token, token_hash = generate_token_pair()
    db.add(
        AuthToken(
            user_type=user_type,
            user_id=user_id,
            token_hash=token_hash,
            purpose=PURPOSE,
            expires_at=datetime.utcnow() + timedelta(minutes=RESET_TTL_MINUTES),
        )
    )
    return raw_token


def reset_link(raw_token: str) -> str:
    return f"{invite_service.frontend_url()}/reset-password?token={raw_token}"


def send_reset_email(
    *,
    to_email: str,
    to_name: str | None,
    raw_token: str,
    user_type: str,
    user_id: int,
    school_id: int | None = None,
) -> None:
    link = reset_link(raw_token)
    outcome = deliver_email(
        to_email,
        "Reset your password — Class Echo",
        _reset_html(to_name=to_name, link=link),
        to_name,
        purpose=PURPOSE,
        school_id=school_id,
        related_type=user_type,
        related_id=user_id,
    )
    if outcome == "failed":
        logger.warning("Password reset email to %s was not delivered.", to_email)


def _reset_html(*, to_name: str | None, link: str) -> str:
    greeting = f"Hi {html.escape(to_name)}," if to_name else "Hi,"
    return f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:24px;background:#FAF9F9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1F2937;">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width:520px;margin:0 auto;background:#FFFFFF;border-radius:16px;border:1px solid #ECECEC;">
      <tr>
        <td style="padding:32px;">
          <p style="margin:0 0 4px;font-size:12px;font-weight:700;letter-spacing:1.2px;color:#0284C7;text-transform:uppercase;">Class Echo</p>
          <h1 style="margin:0 0 16px;font-size:22px;font-weight:800;">{greeting}</h1>
          <p style="margin:0 0 24px;font-size:15px;line-height:1.6;">
            We received a request to reset the password for your Class Echo
            account. Choose a new password using the button below:
          </p>
          <p style="margin:0 0 24px;">
            <a href="{link}" style="display:inline-block;padding:14px 24px;border-radius:12px;background:#0284C7;color:#FFFFFF;font-size:15px;font-weight:700;text-decoration:none;">Reset password</a>
          </p>
          <p style="margin:0 0 8px;font-size:13px;line-height:1.6;color:#6B7280;">
            Or paste this link into your browser:
          </p>
          <p style="margin:0 0 24px;font-size:13px;line-height:1.6;word-break:break-all;">
            <a href="{link}" style="color:#0284C7;">{link}</a>
          </p>
          <p style="margin:0;font-size:13px;line-height:1.6;color:#6B7280;">
            This link expires in {RESET_TTL_MINUTES} minutes and can be used once.
            If you didn't ask to reset your password, you can safely ignore this
            email — your password will not change.
          </p>
        </td>
      </tr>
    </table>
  </body>
</html>"""
