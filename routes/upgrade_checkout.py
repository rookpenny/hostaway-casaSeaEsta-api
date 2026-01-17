# routes/upgrade_checkout.py
import os
import stripe
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from database import get_db
from models import Upgrade, Property, PMCIntegration, UpgradePurchase

router = APIRouter()
stripe.api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").rstrip("/")

def _require():
    if not stripe.api_key:
        raise HTTPException(500, "Missing STRIPE_SECRET_KEY")
    if not APP_BASE_URL:
        raise HTTPException(500, "Missing APP_BASE_URL")

@router.post("/guest/upgrades/{upgrade_id}/checkout")
def create_upgrade_checkout(upgrade_id: int, db: Session = Depends(get_db)):
    _require()

    upgrade = db.query(Upgrade).filter(Upgrade.id == upgrade_id, Upgrade.is_active == True).first()
    if not upgrade:
        raise HTTPException(404, "Upgrade not found")

    prop = db.query(Property).filter(Property.id == upgrade.property_id).first()
    if not prop:
        raise HTTPException(404, "Property not found")

    pmc_id = prop.pmc_id

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.pmc_id == pmc_id, PMCIntegration.provider == "stripe_connect", PMCIntegration.is_connected == True)
        .first()
    )
    if not integ or not integ.account_id:
        raise HTTPException(400, "This property is not accepting upgrade payments yet.")

    # Platform fee (example: 2% + $0.30)
    amount = int(upgrade.price_cents or 0)
    pct_fee = int(round(amount * 0.02))
    flat_fee = 30
    platform_fee = max(0, pct_fee + flat_fee)

    # Create purchase row FIRST
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

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {"name": upgrade.title},
                "unit_amount": amount,
            },
            "quantity": 1,
        }],
        success_url=f"{APP_BASE_URL}/guest/upgrade/success?purchase_id={purchase.id}",
        cancel_url=f"{APP_BASE_URL}/guest/upgrade/cancel?purchase_id={purchase.id}",

        metadata={
            "type": "upgrade_purchase",
            "purchase_id": str(purchase.id),
            "pmc_id": str(pmc_id),
        },
        payment_intent_data={
            "application_fee_amount": platform_fee,
            "metadata": {
                "type": "upgrade_purchase",
                "purchase_id": str(purchase.id),
                "pmc_id": str(pmc_id),
            },
        },

        # Stripe Connect
        stripe_account=integ.account_id,
    )

    purchase.stripe_checkout_session_id = session.id
    db.commit()

    return {"checkout_url": session.url}
