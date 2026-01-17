# routes/stripe_connect.py
import os
import stripe
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from models import PMCIntegration

router = APIRouter()

stripe.api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").rstrip("/")


def _require_env():
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")
    if not APP_BASE_URL:
        raise HTTPException(status_code=500, detail="Missing APP_BASE_URL")


# ✅ Replace this with your real admin auth / session logic
def get_current_pmc_id(request: Request) -> int:
    pmc_id = getattr(request.state, "pmc_id", None)
    if not pmc_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return int(pmc_id)


@router.get("/admin/integrations/stripe/status")
def stripe_connect_status(request: Request, db: Session = Depends(get_db)):
    pmc_id = get_current_pmc_id(request)

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc_id, PMCIntegration.provider == "stripe_connect")
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


@router.post("/admin/integrations/stripe/connect")
def stripe_connect_start(request: Request, db: Session = Depends(get_db)):
    """
    Called by the admin dashboard button.
    Creates (or reuses) a Stripe Express account, then returns an onboarding link URL.
    """
    _require_env()
    pmc_id = get_current_pmc_id(request)

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc_id, PMCIntegration.provider == "stripe_connect")
        .first()
    )

    if not integ:
        integ = PMCIntegration(
            pmc_id=pmc_id,
            provider="stripe_connect",
            is_connected=False,
        )
        db.add(integ)
        db.commit()
        db.refresh(integ)

    # Create account once
    if not integ.account_id:
        acct = stripe.Account.create(
            type="express",
            capabilities={
                "card_payments": {"requested": True},
                "transfers": {"requested": True},
            },
            metadata={"pmc_id": str(pmc_id)},
        )
        integ.account_id = acct["id"]
        db.commit()

    # Create onboarding link (Express)
    link = stripe.AccountLink.create(
        account=integ.account_id,
        refresh_url=f"{APP_BASE_URL}/admin/dashboard?view=settings&tab=integrations",
        return_url=f"{APP_BASE_URL}/admin/integrations/stripe/callback",
        type="account_onboarding",
    )

    return {"url": link["url"]}


@router.get("/admin/integrations/stripe/callback")
def stripe_connect_callback(request: Request, db: Session = Depends(get_db)):
    """
    Stripe returns here after onboarding.
    We fetch the account and mark it connected if charges_enabled.
    """
    _require_env()
    pmc_id = get_current_pmc_id(request)

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc_id, PMCIntegration.provider == "stripe_connect")
        .first()
    )
    if not integ or not integ.account_id:
        return RedirectResponse(url="/admin/dashboard?view=settings&tab=integrations")

    acct = stripe.Account.retrieve(integ.account_id)

    # Update optional columns if they exist
    if hasattr(integ, "charges_enabled"):
        integ.charges_enabled = bool(acct.get("charges_enabled"))
    if hasattr(integ, "payouts_enabled"):
        integ.payouts_enabled = bool(acct.get("payouts_enabled"))
    if hasattr(integ, "details_submitted"):
        integ.details_submitted = bool(acct.get("details_submitted"))

    # Practical definition of "ready"
    integ.is_connected = bool(acct.get("charges_enabled"))

    if hasattr(integ, "connected_at") and integ.is_connected and not getattr(integ, "connected_at", None):
        integ.connected_at = datetime.now(timezone.utc)

    db.commit()

    return RedirectResponse(url="/admin/dashboard?view=settings&tab=integrations")
