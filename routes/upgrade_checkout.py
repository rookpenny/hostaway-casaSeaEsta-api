# routes/upgrade_checkout.py
import os
import stripe
from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Upgrade, Property, PMCIntegration, UpgradePurchase  # add UpgradePurchase model

router = APIRouter()
stripe.api_key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").rstrip("/")


def calc_platform_fee_cents(amount_cents: int) -> int:
    # recommended: 2% with min $0.50 and max $20
    fee = int(round(amount_cents * 0.02))
    return max(50, min(fee, 2000))


@router.post("/guest/upgrades/{upgrade_id}/checkout")
def create_upgrade_checkout(upgrade_id: int):
    if not APP_BASE_URL:
        raise HTTPException(500, "Missing APP_BASE_URL")

    db: Session = SessionLocal()
    try:
        u = db.query(Upgrade).filter(Upgrade.id == upgrade_id, Upgrade.is_active == True).first()
        if not u:
            raise HTTPException(404, "Upgrade not found")

        prop = db.query(Property).filter(Property.id == u.property_id).first()
        if not prop:
            raise HTTPException(404, "Property not found")

        integ = (
            db.query(PMCIntegration)
            .filter(PMCIntegration.pmc_id == prop.pmc_id, PMCIntegration.provider == "stripe_connect")
            .first()
        )
        if not integ or not integ.account_id or not integ.is_connected:
            raise HTTPException(400, "PMC has not connected Stripe")

        amount_cents = int(u.price_cents)
        currency = (getattr(u, "currency", None) or "usd").lower()
        fee_cents = calc_platform_fee_cents(amount_cents)

        purchase = UpgradePurchase(
            pmc_id=prop.pmc_id,
            property_id=prop.id,
            upgrade_id=u.id,
            amount_cents=amount_cents,
            platform_fee_cents=fee_cents,
            currency=currency,
            status="pending",
        )
        db.add(purchase)
        db.commit()
        db.refresh(purchase)

        # Create Checkout on connected account (direct charge)
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": currency,
                        "product_data": {"name": u.title},
                        "unit_amount": amount_cents,
                    },
                    "quantity": 1,
                }
            ],
            success_url=f"{APP_BASE_URL}/guest/upgrade/success?purchase_id={purchase.id}",
            cancel_url=f"{APP_BASE_URL}/guest/upgrade/cancel?purchase_id={purchase.id}",
            payment_intent_data={
                "application_fee_amount": fee_cents,
                "metadata": {
                    "type": "upgrade_purchase",
                    "purchase_id": str(purchase.id),
                    "upgrade_id": str(u.id),
                    "property_id": str(prop.id),
                    "pmc_id": str(prop.pmc_id),
                },
            },
            metadata={
                "type": "upgrade_purchase",
                "purchase_id": str(purchase.id),
                "pmc_id": str(prop.pmc_id),
            },
            stripe_account=integ.account_id,
        )

        purchase.stripe_checkout_session_id = session["id"]
        db.commit()

        return {"checkout_url": session["url"]}
    finally:
        db.close()
