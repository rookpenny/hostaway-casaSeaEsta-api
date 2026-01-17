# routes/stripe_connect.py
import os
import stripe
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from models import PMCIntegration

# IMPORTANT: import your existing auth helper
from routes.admin import get_user_role_and_scope  # <- adjust if your function lives elsewhere

router = APIRouter()

stripe.api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").rstrip("/")


def _require_env():
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")
    if not APP_BASE_URL:
        raise HTTPException(status_code=500, detail="Missing APP_BASE_URL")


def require_pmc_scope(request: Request, db: Session):
    """
    Uses your existing admin auth:
    - request.session["user"] must exist
    - get_user_role_and_scope returns pmc_obj
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_role, pmc_obj, pmc_user, billing_status, needs_payment = get_user_role_and_scope(request, db)

    if user_role != "pmc" or not pmc_obj:
        raise HTTPException(status_code=403, detail="PMC access required")

    return pmc_obj


@router.get("/admin/integrations/stripe/status")
def stripe_connect_status(request: Request, db: Session = Depends(get_db)):
    pmc_obj = require_pmc_scope(request, db)

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc_obj.id, PMCIntegration.provider == "stripe_connect")
        .first()
    )

    if not integ or not integ.account_id:
        return {"connected": False}

    return {
        "connected": bool(integ.is_connected),
        "account_id": integ.account_id,
        "charges_enabled": bool(getattr(integ, "charges_enabled", False)),
        "payouts_enabled": bool(getattr(integ, "payouts_enabled", False)),
        "details_submitted": bool(getattr(integ, "details_submitted", False)),
    }


async function stripeConnectStart() {
  const btn = document.getElementById("stripe-connect-btn");
  btn && (btn.disabled = true);

  try {
    const res = await fetch("/admin/integrations/stripe/connect", {
      method: "POST",
      credentials: "include",
    });

    const contentType = res.headers.get("content-type") || "";
    const raw = await res.text();

    // If it's not JSON, show the raw body (often it's HTML login page)
    if (!contentType.includes("application/json")) {
      console.error("Non-JSON response:", res.status, raw.slice(0, 400));
      alert(`Stripe connect failed (${res.status}). Response was not JSON. Check console.`);
      return;
    }

    const data = JSON.parse(raw);

    if (!res.ok || !data.url) {
      console.error("Stripe connect error:", res.status, data);
      alert(data.detail || `Stripe connect failed (${res.status}).`);
      return;
    }

    window.location.href = data.url;
  } catch (e) {
    console.error(e);
    alert("Stripe connect failed. See console.");
  } finally {
    btn && (btn.disabled = false);
  }
}


@router.get("/admin/integrations/stripe/callback")
def stripe_connect_callback(request: Request, db: Session = Depends(get_db)):
    """
    Stripe returns here after onboarding.
    We fetch account + mark connected if charges_enabled.
    """
    _require_env()
    pmc_obj = require_pmc_scope(request, db)

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc_obj.id, PMCIntegration.provider == "stripe_connect")
        .first()
    )
    if not integ or not integ.account_id:
        return RedirectResponse(url="/admin/dashboard?view=settings&tab=integrations")

    acct = stripe.Account.retrieve(integ.account_id)

    # Update optional columns if they exist on your model/table
    if hasattr(integ, "charges_enabled"):
        integ.charges_enabled = bool(acct.get("charges_enabled"))
    if hasattr(integ, "payouts_enabled"):
        integ.payouts_enabled = bool(acct.get("payouts_enabled"))
    if hasattr(integ, "details_submitted"):
        integ.details_submitted = bool(acct.get("details_submitted"))

    integ.is_connected = bool(acct.get("charges_enabled"))

    if hasattr(integ, "connected_at") and integ.is_connected and not getattr(integ, "connected_at", None):
        integ.connected_at = datetime.now(timezone.utc)

    db.commit()

    return RedirectResponse(url="/admin/dashboard?view=settings&tab=integrations")
