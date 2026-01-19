# routes/upgrade_checkout.py
import os
import stripe
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Upgrade, Property, PMCIntegration, UpgradePurchase

router = APIRouter()

STRIPE_SECRET_KEY = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").rstrip("/")


def _require():
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")
    if not APP_BASE_URL:
        raise HTTPException(status_code=500, detail="Missing APP_BASE_URL")

    stripe.api_key = STRIPE_SECRET_KEY


@router.post("/guest/upgrades/{upgrade_id}/checkout")
def create_upgrade_checkout(upgrade_id: int, db: Session = Depends(get_db)):
    _require()

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

    amount = int(upgrade.price_cents or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid upgrade price")

    # Platform fee (example: 2% + $0.30)
    pct_fee = int(round(amount * 0.02))
    flat_fee = 30
    platform_fee = max(0, pct_fee + flat_fee)

    # Create purchase row FIRST (pending)
    purchase = UpgradePurchase(
        pmc_id=pmc_id,
        property_id=prop.id,
        upgrade_id=upgrade.id,
        amount_cents=amount,
        platform_fee_cents=platform_fee,
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    db.add(purchase)
    db.commit()
    db.refresh(purchase)

    # ✅ Return guest back to main guest experience (guest_app)
    # IMPORTANT: {CHECKOUT_SESSION_ID} must be literal braces for Stripe to substitute
    success_url = (
        f"{APP_BASE_URL}/guest/{prop.id}"
        f"?upgrade_purchase=success"
        f"&purchase_id={purchase.id}"
        f"&session_id={{CHECKOUT_SESSION_ID}}"
        f"#upgrades"
    )
    cancel_url = (
        f"{APP_BASE_URL}/guest/{prop.id}"
        f"?upgrade_purchase=cancel"
        f"&purchase_id={purchase.id}"
        f"#upgrades"
    )

    # ✅ Stripe Checkout session (Destination charge model)
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        line_items=[
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": upgrade.title},
                    "unit_amount": amount,
                },
                "quantity": 1,
            }
        ],
        success_url=success_url,
        cancel_url=cancel_url,

        # Helpful metadata at session level
        metadata={
            "type": "upgrade_purchase",
            "purchase_id": str(purchase.id),
            "pmc_id": str(pmc_id),
            "property_id": str(prop.id),
            "upgrade_id": str(upgrade.id),
        },

        # ✅ Platform fee + transfer to connected account
        payment_intent_data={
            "application_fee_amount": platform_fee,
            "transfer_data": {"destination": integ.account_id},
            "metadata": {
                "type": "upgrade_purchase",
                "purchase_id": str(purchase.id),
                "pmc_id": str(pmc_id),
                "property_id": str(prop.id),
                "upgrade_id": str(upgrade.id),
            },
        },
    )

    purchase.stripe_checkout_session_id = session.id
    db.commit()

    return {"checkout_url": session.url}


@router.get("/guest/upgrade-purchases/{purchase_id}/status")
def upgrade_purchase_status(purchase_id: int, db: Session = Depends(get_db)):
    p = db.query(UpgradePurchase).filter(UpgradePurchase.id == purchase_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Purchase not found")

    return {
        "id": p.id,
        "status": p.status,  # pending|paid|refunded|disputed|failed
        "paid_at": p.paid_at.isoformat() if getattr(p, "paid_at", None) else None,
        "upgrade_id": p.upgrade_id,
        "property_id": p.property_id,
        "amount_cents": p.amount_cents,
        "platform_fee_cents": p.platform_fee_cents,
        "stripe_checkout_session_id": getattr(p, "stripe_checkout_session_id", None),
        "stripe_payment_intent_id": getattr(p, "stripe_payment_intent_id", None),
    }
