# routes/stripe_connect.py
import os
import secrets
import urllib.parse

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from sqlalchemy.orm import Session

from database import get_db
from models import PMCIntegration
from routes.admin import get_user_role_and_scope

router = APIRouter()

# ✅ IMPORTANT:
# STRIPE_CONNECT_CLIENT_ID must be the "ca_..." value from Stripe Connect settings
# (NOT pk_, NOT sk_)
STRIPE_CONNECT_CLIENT_ID = (os.getenv("STRIPE_CONNECT_CLIENT_ID") or "").strip()
STRIPE_SECRET_KEY = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").rstrip("/")


def _require_env():
    missing = []
    if not STRIPE_SECRET_KEY:
        missing.append("STRIPE_SECRET_KEY")
    if not APP_BASE_URL:
        missing.append("APP_BASE_URL")
    if not STRIPE_CONNECT_CLIENT_ID:
        missing.append("STRIPE_CONNECT_CLIENT_ID")
    if missing:
        raise HTTPException(status_code=500, detail=f"Missing env vars: {', '.join(missing)}")

    # Guard against the exact bug you're seeing
    if not STRIPE_CONNECT_CLIENT_ID.startswith("ca_"):
        raise HTTPException(
            status_code=500,
            detail="STRIPE_CONNECT_CLIENT_ID must start with 'ca_' (you set a pk_ or sk_). "
                   "Get it from Stripe Dashboard → Settings → Connect → Integration → Client ID.",
        )


def require_pmc_scope(request: Request, db: Session):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_role, pmc_obj, pmc_user, billing_status, needs_payment = get_user_role_and_scope(request, db)
    if user_role != "pmc" or not pmc_obj:
        raise HTTPException(status_code=403, detail="PMC access required")

    return pmc_obj


# ============================================================
# 1) Status endpoint (admin UI reads this)
# ============================================================
@router.get("/admin/integrations/stripe/status")
def stripe_connect_status(request: Request, db: Session = Depends(get_db)):
    pmc_obj = require_pmc_scope(request, db)

    integ = (
        db.query(PMCIntegration)
        .filter(
            PMCIntegration.pmc_id == pmc_obj.id,
            PMCIntegration.provider == "stripe_connect",
        )
        .first()
    )

    if not integ or not integ.account_id or not integ.is_connected:
        return {"connected": False}

    return {
        "connected": True,
        "account_id": integ.account_id,
        "is_connected": bool(integ.is_connected),
    }


# ============================================================
# 2) Start OAuth (PMCs log into their EXISTING Stripe account)
# ============================================================
@router.post("/admin/integrations/stripe/connect")
def stripe_oauth_start(request: Request, db: Session = Depends(get_db)):
    _require_env()
    pmc_obj = require_pmc_scope(request, db)

    # CSRF state (store per PMC)
    state = secrets.token_urlsafe(24)
    request.session["stripe_oauth_state"] = state

    redirect_uri = f"{APP_BASE_URL}/admin/integrations/stripe/oauth/callback"

    params = {
        "response_type": "code",
        "client_id": STRIPE_CONNECT_CLIENT_ID,  # ✅ must be ca_...
        "scope": "read_write",
        "state": state,
        "redirect_uri": redirect_uri,
    }

    url = "https://connect.stripe.com/oauth/authorize?" + urllib.parse.urlencode(params)

    # ✅ TEMP DEBUG (remove once working)
    # print("[stripe_connect] client_id=", STRIPE_CONNECT_CLIENT_ID)
    # print("[stripe_connect] redirect_uri=", redirect_uri)
    # print("[stripe_connect] url=", url)

    # upsert row (optional but helpful so UI shows "started")
    integ = (
        db.query(PMCIntegration)
        .filter(
            PMCIntegration.pmc_id == pmc_obj.id,
            PMCIntegration.provider == "stripe_connect",
        )
        .first()
    )
    if not integ:
        integ = PMCIntegration(pmc_id=pmc_obj.id, provider="stripe_connect", is_connected=False)
        db.add(integ)
        db.commit()

    return {"url": url}


# ============================================================
# 3) OAuth callback (Stripe redirects here with ?code=...)
# ============================================================
@router.get("/admin/integrations/stripe/oauth/callback")
def stripe_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    _require_env()
    stripe.api_key = STRIPE_SECRET_KEY

    pmc_obj = require_pmc_scope(request, db)

    # If the user cancelled or Stripe returned an error
    if error:
        # tell opener + close
        return HTMLResponse(
            """
<!doctype html><html><body>
<script>
  try {
    if (window.opener) {
      window.opener.postMessage({ type: "stripe_oauth_complete", ok: false }, "*");
      window.close();
    } else {
      window.location.href = "/admin/dashboard?view=settings&tab=integrations";
    }
  } catch (e) {
    window.location.href = "/admin/dashboard?view=settings&tab=integrations";
  }
</script>
</body></html>
            """,
            status_code=200,
        )

    # CSRF check
    expected = request.session.get("stripe_oauth_state")
    request.session.pop("stripe_oauth_state", None)  # one-time use
    if not expected or not state or state != expected:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    if not code:
        raise HTTPException(status_code=400, detail="Missing code")

    # Exchange code for connected account token
    token = stripe.OAuth.token(
        grant_type="authorization_code",
        code=code,
    )

    acct_id = token.get("stripe_user_id")  # acct_...
    if not acct_id:
        raise HTTPException(status_code=400, detail="Stripe did not return stripe_user_id")

    # Upsert integration
    integ = (
        db.query(PMCIntegration)
        .filter(
            PMCIntegration.pmc_id == pmc_obj.id,
            PMCIntegration.provider == "stripe_connect",
        )
        .first()
    )
    if not integ:
        integ = PMCIntegration(pmc_id=pmc_obj.id, provider="stripe_connect")
        db.add(integ)

    integ.account_id = acct_id
    integ.is_connected = True

    # Optional: store if you ever need to act “as” connected account
    # (destination charges do NOT require these)
    if hasattr(integ, "access_token"):
        integ.access_token = token.get("access_token")
    if hasattr(integ, "refresh_token"):
        integ.refresh_token = token.get("refresh_token")

    db.commit()

    # Tell opener to refresh + close popup
    return HTMLResponse(
        """
<!doctype html><html><body>
<script>
  try {
    if (window.opener) {
      window.opener.postMessage({ type: "stripe_oauth_complete", ok: true }, "*");
      window.opener.location.reload();
      window.close();
    } else {
      window.location.href = "/admin/dashboard?view=settings&tab=integrations";
    }
  } catch (e) {
    window.location.href = "/admin/dashboard?view=settings&tab=integrations";
  }
</script>
</body></html>
        """,
        status_code=200,
    )


# ============================================================
# 4) Disconnect (revocation safety)
# ============================================================
@router.post("/admin/integrations/stripe/disconnect")
def stripe_disconnect(request: Request, db: Session = Depends(get_db)):
    _require_env()
    stripe.api_key = STRIPE_SECRET_KEY

    pmc_obj = require_pmc_scope(request, db)

    integ = (
        db.query(PMCIntegration)
        .filter(
            PMCIntegration.pmc_id == pmc_obj.id,
            PMCIntegration.provider == "stripe_connect",
        )
        .first()
    )
    if not integ or not integ.account_id:
        return {"ok": True}

    # ✅ Optional but recommended: revoke access on Stripe if you used OAuth
    # This prevents the platform from retaining access after "disconnect".
    try:
        stripe.OAuth.deauthorize(
            client_id=STRIPE_CONNECT_CLIENT_ID,
            stripe_user_id=integ.account_id,
        )
    except Exception:
        # Don't block disconnect if Stripe revoke fails
        pass

    integ.is_connected = False
    integ.account_id = None

    if hasattr(integ, "access_token"):
        integ.access_token = None
    if hasattr(integ, "refresh_token"):
        integ.refresh_token = None

    db.commit()
    return {"ok": True}
