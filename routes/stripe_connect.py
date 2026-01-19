# routes/stripe_connect.py
import os
import stripe
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from sqlalchemy.orm import Session

from database import get_db
from models import PMCIntegration
from routes.admin import get_user_role_and_scope  # <-- your correct import

router = APIRouter()

APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").rstrip("/")
STRIPE_SECRET_KEY = (os.getenv("STRIPE_SECRET_KEY") or "").strip()


def _require_env():
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")
    if not APP_BASE_URL:
        raise HTTPException(status_code=500, detail="Missing APP_BASE_URL")


def require_pmc_scope(request: Request, db: Session):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_role, pmc_obj, pmc_user, billing_status, needs_payment = get_user_role_and_scope(request, db)
    if user_role != "pmc" or not pmc_obj:
        raise HTTPException(status_code=403, detail="PMC access required")

    return pmc_obj


@router.get("/admin/integrations/stripe/status")
@router.get("/admin/integrations/stripe/status")
def stripe_connect_status(request: Request, db: Session = Depends(get_db)):
    try:
        pmc_obj = require_pmc_scope(request, db)
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc_obj.id, PMCIntegration.provider == "stripe_connect")
        .first()
    )

    if not integ or not integ.account_id:
        return {"connected": False, "ready": False}

    # connected = account exists
    connected = True

    charges_enabled = bool(getattr(integ, "charges_enabled", False))
    payouts_enabled = bool(getattr(integ, "payouts_enabled", False))
    details_submitted = bool(getattr(integ, "details_submitted", False))

    return {
        "connected": connected,
        "ready": bool(charges_enabled),   # "can accept payments"
        "account_id": integ.account_id,
        "charges_enabled": charges_enabled,
        "payouts_enabled": payouts_enabled,
        "details_submitted": details_submitted,
    }



@router.post("/admin/integrations/stripe/connect")
def stripe_connect_start(request: Request, db: Session = Depends(get_db)):
    """
    Called by the admin dashboard 'Connect Stripe' button.
    Creates (or reuses) a Stripe Express account and returns onboarding link URL.
    """
    try:
        _require_env()
        pmc_obj = require_pmc_scope(request, db)
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)

    stripe.api_key = STRIPE_SECRET_KEY

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc_obj.id, PMCIntegration.provider == "stripe_connect")
        .first()
    )

    if not integ:
        integ = PMCIntegration(
            pmc_id=pmc_obj.id,
            provider="stripe_connect",
            is_connected=False,
        )
        db.add(integ)
        db.commit()
        db.refresh(integ)

    # Create the connected account once
    if not integ.account_id:
        try:
            acct = stripe.Account.create(
                type="express",
                capabilities={
                    "card_payments": {"requested": True},
                    "transfers": {"requested": True},
                },
                metadata={"pmc_id": str(pmc_obj.id)},
            )
            integ.account_id = acct["id"]
            db.commit()
        except Exception as e:
            db.rollback()
            return JSONResponse({"detail": f"Stripe account create failed: {str(e)}"}, status_code=500)

    # Create onboarding link
    try:
        link = stripe.AccountLink.create(
            account=integ.account_id,
            refresh_url=f"{APP_BASE_URL}/admin/dashboard?view=settings&tab=integrations",
            return_url=f"{APP_BASE_URL}/admin/integrations/stripe/callback?popup=1",
            type="account_onboarding",
        )
        return {"url": link["url"]}
    except Exception as e:
        return JSONResponse({"detail": f"Stripe account link failed: {str(e)}"}, status_code=500)


@router.get("/admin/integrations/stripe/callback")
def stripe_connect_callback(request: Request, db: Session = Depends(get_db), popup: int = 0):
    _require_env()
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
        # nothing to update
        if popup:
            return HTMLResponse(
                "<script>try{window.opener&&window.opener.location.reload()}catch(e){};window.close();</script>"
            )
        return RedirectResponse(url="/admin/dashboard?view=settings&tab=integrations")

    # ✅ Pull fresh truth from Stripe and write it to DB
    acct = stripe.Account.retrieve(integ.account_id)

    if hasattr(integ, "charges_enabled"):
        integ.charges_enabled = bool(acct.get("charges_enabled"))
    if hasattr(integ, "payouts_enabled"):
        integ.payouts_enabled = bool(acct.get("payouts_enabled"))
    if hasattr(integ, "details_submitted"):
        integ.details_submitted = bool(acct.get("details_submitted"))

    # IMPORTANT:
    # "connected" in UX should mean account exists (saved),
    # but "ready to charge" is charges_enabled.
    integ.is_connected = True  # account connected (onboarding may still be incomplete)

    if hasattr(integ, "connected_at") and not getattr(integ, "connected_at", None):
        integ.connected_at = datetime.now(timezone.utc)

    db.commit()

    # ✅ If popup=1, close window + refresh opener
    if popup:
        return HTMLResponse(
            """
<!doctype html>
<html><body>
<script>
  try {
    if (window.opener) {
      window.opener.location.reload();
    }
  } catch(e) {}
  setTimeout(() => window.close(), 250);
</script>
</body></html>
"""
        )

    return RedirectResponse(url="/admin/dashboard?view=settings&tab=integrations")


@router.post("/admin/integrations/stripe/disconnect")
def stripe_connect_disconnect(request: Request, db: Session = Depends(get_db)):
    pmc_obj = require_pmc_scope(request, db)

    integ = (
        db.query(PMCIntegration)
        .filter(
            PMCIntegration.pmc_id == pmc_obj.id,
            PMCIntegration.provider == "stripe_connect",
        )
        .first()
    )

    if not integ:
        return {"ok": True}

    # ❗ Do NOT delete the Stripe account on Stripe's side
    # Just unlink it from this PMC
    integ.is_connected = False
    integ.charges_enabled = False
    integ.payouts_enabled = False

    db.commit()

    return {"ok": True}



@router.get("/admin/integrations/stripe/close", response_class=HTMLResponse)
def stripe_connect_close():
    return """
<!doctype html>
<html>
  <head>
    <title>Stripe Connected</title>
    <meta charset="utf-8" />
  </head>
  <body style="font-family: system-ui; text-align: center; padding: 40px;">
    <h2>✅ Stripe connected</h2>
    <p>You can close this window.</p>

    <script>
      try {
        if (window.opener) {
          window.opener.location.reload();
        }
      } catch (e) {}

      setTimeout(() => {
        window.close();
      }, 400);
    </script>
  </body>
</html>
"""
