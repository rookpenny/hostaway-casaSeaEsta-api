import os
import logging
import resend

log = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")

def email_enabled() -> bool:
    return bool(RESEND_API_KEY and EMAIL_FROM and APP_BASE_URL)

def send_invite_email(*, to_email: str, invited_by: str, pmc_name: str):
    """
    Sends invite email via Resend.
    Returns provider response dict on success; raises on failure.
    """
    if not email_enabled():
        raise RuntimeError("Email not configured: missing RESEND_API_KEY / EMAIL_FROM / APP_BASE_URL")

    resend.api_key = RESEND_API_KEY

    subject = f"You’ve been added to {pmc_name} on HostScout"
    login_url = f"{APP_BASE_URL}/auth/login/google?next=/admin/dashboard#settings?tab=team"

    html = f"""
    <div style="font-family: ui-sans-serif, system-ui; line-height: 1.5;">
      <h2>You’ve been added to <b>{pmc_name}</b> ✅</h2>
      <p><b>{invited_by}</b> added you as a team member in HostScout.</p>
      <p>To access your account, sign in with Google using <b>{to_email}</b>:</p>
      <p><a href="{login_url}" style="display:inline-block;background:#0f172a;color:#fff;padding:10px 14px;border-radius:10px;text-decoration:none;">
        Sign in to HostScout
      </a></p>
      <p style="color:#64748b;font-size:12px;margin-top:16px;">If you didn’t expect this, you can ignore this email.</p>
    </div>
    """

    return resend.Emails.send({
        "from": EMAIL_FROM,
        "to": [to_email],
        "subject": subject,
        "html": html,
    })
