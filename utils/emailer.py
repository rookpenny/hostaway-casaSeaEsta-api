import os
import logging
import resend

log = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")

def email_enabled() -> bool:
    return bool(RESEND_API_KEY and EMAIL_FROM and APP_BASE_URL)




def send_invite_email(*, to_email: str, invited_by: str, pmc_name: str) -> bool:
    """
    Sends invite email via Resend.

    Returns:
      True if the provider accepted the send request.
      False if email is not configured or provider send fails.

    Notes:
      - Resend requires EMAIL_FROM to be from a VERIFIED domain (not gmail.com).
      - This function should NOT raise in normal operation—invites should still succeed even if email fails.
    """
    if not email_enabled():
        # Don't hard-fail the invite flow if email isn't configured yet.
        return False

    resend.api_key = RESEND_API_KEY

    subject = f"You’ve been added to {pmc_name} on HostScout"

    # Build a safe login URL and keep the settings tab state
    base = (APP_BASE_URL or "").rstrip("/")
    login_url = f"{base}/auth/login/google?next=/admin/dashboard#settings?tab=team"

    safe_pmc_name = (pmc_name or "your team").strip()
    safe_invited_by = (invited_by or "A teammate").strip()
    safe_to_email = (to_email or "").strip()

    html = f"""
    <div style="font-family: ui-sans-serif, system-ui; line-height: 1.5;">
      <h2>You’ve been added to <b>{safe_pmc_name}</b> ✅</h2>
      <p><b>{safe_invited_by}</b> added you as a team member in HostScout.</p>
      <p>To access your account, sign in with Google using <b>{safe_to_email}</b>:</p>
      <p>
        <a href="{login_url}"
           style="display:inline-block;background:#0f172a;color:#fff;padding:10px 14px;border-radius:10px;text-decoration:none;">
          Sign in to HostScout
        </a>
      </p>
      <p style="color:#64748b;font-size:12px;margin-top:16px;">
        If you didn’t expect this, you can ignore this email.
      </p>
    </div>
    """

    try:
        resend.Emails.send(
            {
                "from": EMAIL_FROM,          # must be from your verified domain
                "to": [safe_to_email],
                "subject": subject,
                "html": html,
            }
        )
        return True
    except Exception:
        # Log at the call site (route) so you get stack traces there
        return False


def send_upgrade_purchase_email(
    *,
    to_emails: list[str],
    pmc_name: str,
    property_name: str,
    upgrade_title: str,
    amount_cents: int,
    currency: str,
    guest_name: str | None = None,
    arrival_date: str | None = None,
    departure_date: str | None = None,
    purchase_id: int | None = None,
    property_id: int | None = None,
    upgrade_id: int | None = None,
) -> bool:
    """
    Sends an "Upgrade purchased" notification email via Resend.
    Returns True if provider accepted send; False otherwise.
    """
    if not email_enabled():
        return False

    resend.api_key = RESEND_API_KEY

    # normalize + de-dupe
    tos = sorted({(e or "").strip().lower() for e in (to_emails or []) if e and "@" in e})
    if not tos:
        return False

    safe_pmc_name = (pmc_name or "Your team").strip()
    safe_property_name = (property_name or "Your property").strip()
    safe_upgrade_title = (upgrade_title or "Upgrade").strip()
    safe_guest_name = (guest_name or "").strip()

    amt = (int(amount_cents or 0) / 100.0)
    cur = (currency or "usd").upper().strip()

    subject = f"[HostScout] New upgrade purchase: {safe_upgrade_title} — {safe_property_name}"

    # Optional admin deep link (adjust if you have a different route)
    base = (APP_BASE_URL or "").rstrip("/")
    admin_url = f"{base}/admin/dashboard#messages"  # tweak to your actual messages tab

    stay_line = ""
    if arrival_date and departure_date:
        stay_line = f"{arrival_date} → {departure_date}"

    meta_bits = []
    if purchase_id: meta_bits.append(f"Purchase ID: {purchase_id}")
    if property_id: meta_bits.append(f"Property ID: {property_id}")
    if upgrade_id: meta_bits.append(f"Upgrade ID: {upgrade_id}")
    meta_block = "<br/>".join(meta_bits) if meta_bits else ""

    html = f"""
    <div style="font-family: ui-sans-serif, system-ui; line-height: 1.5;">
      <h2>New upgrade purchase ✅</h2>

      <p><b>PMC:</b> {safe_pmc_name}</p>
      <p><b>Property:</b> {safe_property_name}</p>
      <p><b>Upgrade:</b> {safe_upgrade_title}</p>
      <p><b>Amount:</b> {amt:.2f} {cur}</p>

      {"<p><b>Guest:</b> " + safe_guest_name + "</p>" if safe_guest_name else ""}
      {"<p><b>Stay:</b> " + stay_line + "</p>" if stay_line else ""}

      {f"<p style='color:#64748b;font-size:12px;margin-top:12px;'>{meta_block}</p>" if meta_block else ""}

      <p style="margin-top:16px;">
        <a href="{admin_url}"
           style="display:inline-block;background:#0f172a;color:#fff;padding:10px 14px;border-radius:10px;text-decoration:none;">
          View in Admin
        </a>
      </p>

      <p style="color:#64748b;font-size:12px;margin-top:16px;">
        You’re receiving this because notifications are enabled for your PMC.
      </p>
    </div>
    """

    try:
        resend.Emails.send(
            {
                "from": EMAIL_FROM,
                "to": tos,
                "subject": subject,
                "html": html,
            }
        )
        return True
    except Exception:
        return False

