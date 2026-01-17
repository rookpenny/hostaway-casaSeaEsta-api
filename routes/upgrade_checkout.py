# routes/upgrade_checkout.py
import os
import stripe
from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Upgrade, Property, PMCIntegration, UpgradePurchase

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")


@router.post("/guest/upgrades/{upgrade_id}/checkout")
def create_upgrade_checkout(upgrade_id: int):
    db: Session = SessionLocal()
    try:
        # 1) Load upgrade
        upgrade = (
            db.query(Upgrade)
            .filter(Upgrade.id == upgrade_id, Upgrade.is_active == True)
            .first()
        )
        if not upgrade:
            raise HTTPException(404, "Upgrade not found")

        # 2) Load property
        prop = db.query(Property).filter(Property.id == upgrade.property_id).first()
        if not prop:
            raise HTTPException(404, "Property not found")

        pmc_id = prop.pmc_id

        # 3) Load Stripe Connect account
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
            raise HTTPException(400, "PMC has not connected Stripe")

        # 4) Create DB purchase record FIRST
        purchase = UpgradePurchase(
            pmc_id=pmc_id,
            property_id=prop.id,
            upgrade_id=upgrade.id,
            amount_cents=upgrade.price_cents,
            platform_fee_cents=int(upgrade.price_cents * 0.02),  # example 2%
            status="pending",
        )
        db.add(purchase)
        db.commit()
        db.refresh(purchase)

        # 5) CREATE STRIPE CHECKOUT SESSION  ← METADATA GOES HERE
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": upgrade.title,
                        },
                        "unit_amount": upgrade.price_cents,
                    },
                    "quantity": 1,
                }
            ],
            success_url=f"{APP_BASE_URL}/guest/upgrade/success?purchase_id={purchase.id}",
            cancel_url=f"{APP_BASE_URL}/guest/upgrade/cancel?purchase_id={purchase.id}",

            # 👇👇👇 THIS IS WHERE IT GOES 👇👇👇
            metadata={
                "type": "upgrade_purchase",
                "purchase_id": str(purchase.id),
                "pmc_id": str(pmc_id),
            },

            payment_intent_data={
                "application_fee_amount": purchase.platform_fee_cents,
                "metadata": {
                    # duplicate here is OK and recommended
                    "type": "upgrade_purchase",
                    "purchase_id": str(purchase.id),
                    "pmc_id": str(pmc_id),
                },
            },

            # 👇 required for Stripe Connect
            stripe_account=integ.account_id,
        )

        # 6) Save checkout session id
        purchase.stripe_checkout_session_id = session.id
        db.commit()

        return {"checkout_url": session.url}

    finally:
        db.close()
