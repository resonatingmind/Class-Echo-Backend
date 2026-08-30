"""Creating and emailing invite links.

Both onboarding entry points — the super admin setting up an organisation and a
school admin adding a teacher — issue the same kind of invite, so the token
lifecycle and the email template live here rather than being copy-pasted into
each router.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import generate_token_pair
from app.models.auth_token import AuthToken
from app.services.email_service import deliver_email

logger = logging.getLogger(__name__)

# Fallback only. The live value comes from settings_service so a school that
# needs two weeks to onboard its staff does not need a redeploy to get it.
INVITE_TTL_DAYS = 7


async def issue_invite_token(db: AsyncSession, user_type: str, user_id: int) -> str:
    """Mint a fresh invite token and return the raw (emailable) half.

    Any earlier unused invite for the same account is burned first. Without that,
    re-inviting someone would leave several live links pointing at one account,
    so revoking a mis-sent invite by re-sending would not actually revoke it.
    """
    await db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_type == user_type,
            AuthToken.user_id == user_id,
            AuthToken.purpose == "invite",
            AuthToken.used_at.is_(None),
        )
        .values(used_at=datetime.utcnow())
    )

    raw_token, token_hash = generate_token_pair()
    db.add(
        AuthToken(
            user_type=user_type,
            user_id=user_id,
            token_hash=token_hash,
            purpose="invite",
            expires_at=datetime.utcnow() + timedelta(days=invite_ttl_days()),
        )
    )
    return raw_token


def invite_ttl_days() -> int:
    from app.services import settings_service

    try:
        return int(settings_service.get("invite_ttl_days"))
    except Exception:
        return INVITE_TTL_DAYS


def frontend_url() -> str:
    """The base every emailed link is built on, overridable at runtime.

    Wrapped rather than read inline because the same value is needed from the
    verification links in auth.py, and getting these two out of step is exactly
    the bug this is meant to make fixable without a deploy.
    """
    from app.services import settings_service

    try:
        return str(settings_service.get("frontend_url")).rstrip("/")
    except Exception:
        return settings.FRONTEND_URL.rstrip("/")


def invite_link(raw_token: str) -> str:
    return f"{frontend_url()}/accept-invite?token={raw_token}"


def queue_invite_email(
    *,
    to_email: str,
    to_name: str,
    organisation_name: str,
    role_label: str,
    raw_token: str,
    inviter_name: str | None = None,
    school_id: int | None = None,
    related_type: str | None = None,
    related_id: int | None = None,
) -> None:
    """Build the invitation and get it delivered.

    Prefers the Celery worker so a slow or down provider cannot make the invite
    endpoint time out after the account row was already committed.
    """
    # Without this, a deployment missing its email credentials reports a
    # successful invite while the message only ever reaches the log.
    if settings.email_backend == "log":
        logger.warning(
            "No email provider is configured: the invitation for %s was NOT "
            "delivered. Set the MAILJET_* (or SMTP_*) variables on this "
            "deployment and resend the invitation.",
            to_email,
        )

    link = invite_link(raw_token)
    subject = f"You've been invited to {organisation_name} on Class Echo"
    html = _invite_html(
        to_name=to_name,
        organisation_name=organisation_name,
        role_label=role_label,
        link=link,
        inviter_name=inviter_name,
    )
    # deliver_email picks the worker or an inline send and swallows failures:
    # the account row is already committed, so 500-ing here would report failure
    # for an invitation that was in fact created.
    outcome = deliver_email(
        to_email,
        subject,
        html,
        to_name,
        purpose="invite",
        school_id=school_id,
        related_type=related_type,
        related_id=related_id,
    )
    if outcome == "failed":
        logger.warning(
            "Invite email to %s was not delivered. Resend it from the "
            "organisation screen, or set their password directly.",
            to_email,
        )


def _invite_html(
    *,
    to_name: str,
    organisation_name: str,
    role_label: str,
    link: str,
    inviter_name: str | None,
) -> str:
    invited_by = f" by {_escape(inviter_name)}" if inviter_name else ""
    return f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:24px;background:#FAF9F9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1F2937;">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width:520px;margin:0 auto;background:#FFFFFF;border-radius:16px;border:1px solid #ECECEC;">
      <tr>
        <td style="padding:32px;">
          <p style="margin:0 0 4px;font-size:12px;font-weight:700;letter-spacing:1.2px;color:#0284C7;text-transform:uppercase;">Class Echo</p>
          <h1 style="margin:0 0 16px;font-size:22px;font-weight:800;">Hi {_escape(to_name)},</h1>
          <p style="margin:0 0 16px;font-size:15px;line-height:1.6;">
            You've been invited{invited_by} to join
            <strong>{_escape(organisation_name)}</strong> on Class Echo as a
            <strong>{_escape(role_label)}</strong>.
          </p>
          <p style="margin:0 0 24px;font-size:15px;line-height:1.6;">
            Set your password to activate your account:
          </p>
          <p style="margin:0 0 24px;">
            <a href="{link}" style="display:inline-block;padding:14px 24px;border-radius:12px;background:#0284C7;color:#FFFFFF;font-size:15px;font-weight:700;text-decoration:none;">Accept invitation</a>
          </p>
          <p style="margin:0 0 8px;font-size:13px;line-height:1.6;color:#6B7280;">
            Or paste this link into your browser:
          </p>
          <p style="margin:0 0 24px;font-size:13px;line-height:1.6;word-break:break-all;">
            <a href="{link}" style="color:#0284C7;">{link}</a>
          </p>
          <p style="margin:0;font-size:13px;line-height:1.6;color:#6B7280;">
            This link expires in {INVITE_TTL_DAYS} days. If you weren't expecting
            this invitation you can safely ignore this email.
          </p>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def _escape(value: str | None) -> str:
    """Names come from admin input and land inside an HTML email."""
    import html

    return html.escape(value or "")
