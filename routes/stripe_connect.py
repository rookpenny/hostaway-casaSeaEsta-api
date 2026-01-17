# routes/stripe_connect.py
import os
import stripe
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import SessionLocal
from models import PMCIntegration  # create if not exists

router = APIRouter()

stripe.api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").rstrip("/")


def require_user_pmc_id(request: Request) -> int:
    """
    Replace this with YOUR auth/session logic.
    Must return pmc_id for the logged-in PMC user.
    """
    user = getattr(request.state, "user", None)
    pmc_id = getattr(user, "pmc_id", None) if user else None
    if not pmc_id:
        raise HTTPException(401, "Unauthorized")
    return int(pmc_id)


@router.get("/admin/integrations/stripe/status")
def stripe_status(request: Request):
    pmc_id = require_user_pmc_id(request)
    db: Session = SessionLocal()
    try:
        integ = (
            db.query(PMCIntegration)
            .filter(PMCIntegration.pmc_id == pmc_id, PMCIntegration.provider == "stripe_connect")
            .first()
        )
        if not integ:
            return {"connected": False, "integration": None}

        return {
            "connected": bool(integ.is_connected),
            "integration": {
                "account_id": integ.account_id,
                "is_connected": integ.is_connected,
                "charges_enabled": getattr(integ, "charges_enabled", False),
                "payouts_enabled": getattr(integ, "payouts_enabled", False),
                "details_submitted": getattr(integ, "details_submitted", False),
            },
        }
    finally:
        db.close()


@router.post("/admin/integrations/stripe/connect/start")
def stripe_connect_start(request: Request):
    if not APP_BASE_URL:
        raise HTTPException(500, "Missing APP_BASE_URL")

    pmc_id = require_user_pmc_id(request)
    db: Session = SessionLocal()
    try:
        integ = (
            db.query(PMCIntegration)
            .filter(PMCIntegration.pmc_id == pmc_id, PMCIntegration.provider == "stripe_connect")
            .first()
        )

        acct_id = integ.account_id if integ else None

        if not acct_id:
            acct = stripe.Account.create(
                type="express",
                capabilities={
                    "card_payments": {"requested": True},
                    "transfers": {"requested": True},
                },
            )
            acct_id = acct["id"]

            if not integ:
                integ = PMCIntegration(
                    pmc_id=pmc_id,
                    provider="stripe_connect",
                    account_id=acct_id,
                    is_connected=False,
                )
                db.add(integ)
            else:
                integ.account_id = acct_id

            db.commit()

        refresh_url = f"{APP_BASE_URL}/admin/dashboard?view=settings&settings=integrations&stripe=refresh"
        return_url = f"{APP_BASE_URL}/admin/integrations/stripe/connect/return"

        link = stripe.AccountLink.create(
            account=acct_id,
            refresh_url=refresh_url,
            return_url=return_url,
            type="account_onboarding",
        )

        return {"url": link["url"]}
    finally:
        db.close()


@router.get("/admin/integrations/stripe/connect/return")
def stripe_connect_return(request: Request):
    pmc_id = require_user_pmc_id(request)
    db: Session = SessionLocal()
    try:
        integ = (
            db.query(PMCIntegration)
            .filter(PMCIntegration.pmc_id == pmc_id, PMCIntegration.provider == "stripe_connect")
            .first()
        )
        if not integ or not integ.account_id:
            raise HTTPException(400, "Missing Stripe connected account")

        acct = stripe.Account.retrieve(integ.account_id)

        integ.is_connected = True
        if hasattr(integ, "charges_enabled"):
            integ.charges_enabled = bool(acct.get("charges_enabled"))
        if hasattr(integ, "payouts_enabled"):
            integ.payouts_enabled = bool(acct.get("payouts_enabled"))
        if hasattr(integ, "details_submitted"):
            integ.details_submitted = bool(acct.get("details_submitted"))

        db.commit()

        return RedirectResponse(
            url=f"{APP_BASE_URL}/admin/dashboard?view=settings&settings=integrations&stripe=connected",
            status_code=303,
        )
    finally:
        db.close()
