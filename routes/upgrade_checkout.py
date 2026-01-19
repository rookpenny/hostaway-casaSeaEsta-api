# routes/upgrade_checkout.py
import os
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session

from database import get_db
from models import Upgrade, Property, PMCIntegration, UpgradePurchase

router = APIRouter()

STRIPE_SECRET_KEY = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").rstrip("/")
STRIPE_CLIENT_ID = (os.getenv("STRIPE_CLIENT_ID") or "").strip()

def _require():
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")
    if not APP_BASE_URL:
        raise HTTPException(status_code=500, detail="Missing APP_BASE_URL")


@router.post("/guest/upgrades/{upgrade_id}/checkout")
def create_upgrade_checkout(upgrade_id: int, db: Session = Depends(get_db)):
    _require()
    stripe.api_key = STRIPE_SECRET_KEY

    upgrade = (
        db.query(Upgrade)
        .filter(Upgrade.id == upgrade_id, Upgrade.is_active == True)
        .first()
    )
    if not upgrade:
        raise HTTPException(status_code=404, detail="Upgrade not found")

    prop = db.query(Property).filter(Property.id == upgrade.property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    pmc_id = prop.pmc_id

    # Stripe Connect integration
    integ = (
        db.query(PMCIntegration)
        .filter(
            PMCIntegration.pmc_id == pmc_id,
            PMCIntegration.provider == "stripe_connect",
            PMCIntegration.is_connected == True,
        )
        .first()
    )
    if not integ or not integ.account_id:
        raise HTTPException(
            status_code=403,
            detail="This property is not accepting upgrade payments yet.",
        )

    # Amount + platform fee (example: 2% + $0.30)
    amount = int(upgrade.price_cents or 0)
    pct_fee = int(round(amount * 0.02))
    flat_fee = 30
    platform_fee = max(0, pct_fee + flat_fee)

    # 🔒 Guard invalid amounts / fees
    if amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="Invalid upgrade amount."
        )
    
    # Platform fee can never exceed total charge
    if platform_fee >= amount:
        platform_fee = amount - 1  # leave at least $0.01 for PMC
    
    if platform_fee < 0:
        platform_fee = 0


    # Create purchase row FIRST (pending)
    purchase = UpgradePurchase(
        pmc_id=pmc_id,
        property_id=prop.id,
        upgrade_id=upgrade.id,
        amount_cents=amount,
        platform_fee_cents=platform_fee,
        status="pending",
    )
    db.add(purchase)
    db.commit()
    db.refresh(purchase)

    # ✅ Store destination + net amount for reconciliation
    purchase.stripe_destination_account_id = integ.account_id
    purchase.net_amount_cents = amount - platform_fee

    if purchase.net_amount_cents <= 0:
        raise HTTPException(
            status_code=400,
            detail="Invalid upgrade pricing configuration.",
        )
    
    db.commit()


    # ✅ Redirect guest back to your main guest experience (guest_app)
    # IMPORTANT: keep {CHECKOUT_SESSION_ID} exactly like this so Stripe fills it.
    
    success_url = (
        f"{APP_BASE_URL}/guest/{prop.id}"
        f"?screen=upgrades"
        f"&upgrade=success"
        f"&purchase_id={purchase.id}"
        f"&upgrade_id={upgrade.id}"
        f"&session_id={{CHECKOUT_SESSION_ID}}"
    )


    cancel_url = (
        f"{APP_BASE_URL}/guest/{prop.id}"
        f"?screen=upgrades"
        f"&upgrade=cancel"
        f"&purchase_id={purchase.id}"
        f"&upgrade_id={upgrade.id}"
    )



    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {"name": upgrade.title},
                "unit_amount": amount,
            },
            "quantity": 1,
        }],
        success_url=success_url,
        cancel_url=cancel_url,
    
        metadata={
            "type": "upgrade_purchase",
            "purchase_id": str(purchase.id),
            "pmc_id": str(pmc_id),
            "property_id": str(prop.id),
            "upgrade_id": str(upgrade.id),
        },
    
        # ✅ THIS is what routes funds to the PMC
        payment_intent_data={
            "application_fee_amount": platform_fee,
            "transfer_data": {"destination": integ.account_id},  # ✅ PMC gets the money
            "metadata": {
                "type": "upgrade_purchase",
                "purchase_id": str(purchase.id),
                "pmc_id": str(pmc_id),
                "property_id": str(prop.id),
                "upgrade_id": str(upgrade.id),
            },
        },
    
        # ❌ DO NOT set stripe_account here for destination charges
        # stripe_account=integ.account_id,
    )


    purchase.stripe_checkout_session_id = session.id
    db.commit()

    return {"checkout_url": session.url, "purchase_id": purchase.id}


# ✅ Purchase status endpoint (guest UI calls this after redirect)
@router.get("/guest/upgrades/purchase-status")
def upgrade_purchase_status(
    purchase_id: int = Query(...),
    session_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    p = db.query(UpgradePurchase).filter(UpgradePurchase.id == purchase_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Purchase not found")

    # Optional safety: if session_id provided, require it to match what we stored
    if session_id and getattr(p, "stripe_checkout_session_id", None):
        if session_id != p.stripe_checkout_session_id:
            raise HTTPException(status_code=403, detail="Invalid session for purchase")

    status = (getattr(p, "status", "") or "pending").lower()

    return {
        "purchase_id": p.id,
        "status": status,  # pending|paid|refunded|failed...
        "upgrade_id": getattr(p, "upgrade_id", None),
        "property_id": getattr(p, "property_id", None),
        "paid": status == "paid",
        "refunded": status == "refunded",
    }
