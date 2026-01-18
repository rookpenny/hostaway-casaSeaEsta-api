# routes/upgrade_checkout.py
import os
import stripe
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import Upgrade, Property, PMCIntegration, UpgradePurchase

router = APIRouter()

stripe.api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").rstrip("/")


def _require():
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")
    if not APP_BASE_URL:
        raise HTTPException(status_code=500, detail="Missing APP_BASE_URL")


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

    # Stripe Connect integration (one query)
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

    # ✅ Send guest back to the main guest experience (guest_app)
    # NOTE: CHECKOUT_SESSION_ID must be literal braces for Stripe
    success_url = (
        f"{APP_BASE_URL}/guest/{prop.id}"
        f"?upgrade=success"
        f"&purchase_id={purchase.id}"
        f"&session_id={{CHECKOUT_SESSION_ID}}"
    )
    cancel_url = (
        f"{APP_BASE_URL}/guest/{prop.id}"
        f"?upgrade=cancel"
        f"&purchase_id={purchase.id}"
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

        # Keep a little metadata at session level too (nice for debugging)
        metadata={
            "type": "upgrade_purchase",
            "purchase_id": str(purchase.id),
            "pmc_id": str(pmc_id),
            "property_id": str(prop.id),
            "upgrade_id": str(upgrade.id),
        },

        # ✅ Destination charge + platform fee (recommended)
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
