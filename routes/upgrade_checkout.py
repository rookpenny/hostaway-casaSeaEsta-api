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


def _require() -> None:
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")
    if not APP_BASE_URL:
        raise HTTPException(status_code=500, detail="Missing APP_BASE_URL")


def _calc_platform_fee(amount_cents: int) -> int:
    """
    Example platform fee: 2% + $0.30
    Ensures fee is never >= amount.
    """
    pct_fee = int(round(amount_cents * 0.02))
    flat_fee = 30
    fee = max(0, pct_fee + flat_fee)

    if fee >= amount_cents:
        fee = max(0, amount_cents - 1)  # leave at least $0.01 for destination
    return fee


@router.post("/guest/upgrades/{upgrade_id}/checkout")
def create_upgrade_checkout(
    upgrade_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
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

    # ✅ Require guest unlock for this property
    verified_flag = request.session.get(f"guest_verified_{prop.id}", False)
    if not verified_flag:
        raise HTTPException(
            status_code=403,
            detail="Please unlock your stay before purchasing upgrades.",
        )

    pmc_id = prop.pmc_id

    # ✅ Stripe Connect integration (destination account)
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

    amount = int(getattr(upgrade, "price_cents", 0) or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid upgrade amount.")

    currency = (getattr(upgrade, "currency", None) or "usd").lower().strip()
    platform_fee = _calc_platform_fee(amount)
    net_amount = amount - platform_fee
    if net_amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid upgrade pricing configuration.")

    # 1) Create purchase row FIRST (pending)
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

    # Store destination + net amount for reconciliation (if your model has these fields)
    if hasattr(purchase, "stripe_destination_account_id"):
        purchase.stripe_destination_account_id = integ.account_id
    if hasattr(purchase, "net_amount_cents"):
        purchase.net_amount_cents = net_amount
    db.commit()

    # 2) Build redirect URLs (purchase_id must be the DB integer)
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

    # 3) Create Stripe Checkout Session
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            client_reference_id=str(purchase.id),
            line_items=[
                {
                    "price_data": {
                        "currency": currency,
                        "product_data": {"name": upgrade.title or "Upgrade"},
                        "unit_amount": amount,
                    },
                    "quantity": 1,
                }
            ],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "type": "upgrade_purchase",
                "purchase_id": str(purchase.id),
                "pmc_id": str(pmc_id),
                "property_id": str(prop.id),
                "upgrade_id": str(upgrade.id),
            },
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
    except Exception as e:
        # mark failed so it doesn't sit "pending" forever
        try:
            purchase.status = "failed"
            db.add(purchase)
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=500, detail="Unable to start checkout for this upgrade.") from e

    # 4) Save Stripe session id
    if hasattr(purchase, "stripe_checkout_session_id"):
        purchase.stripe_checkout_session_id = session.id
    db.add(purchase)
    db.commit()

    return {"checkout_url": session.url, "purchase_id": purchase.id}


@router.get("/guest/upgrades/purchase-status")
def upgrade_purchase_status(
    purchase_id: int = Query(...),
    session_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    p = db.query(UpgradePurchase).filter(UpgradePurchase.id == purchase_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Purchase not found")

    # Optional safety: if session_id provided, require it matches what we stored
    stored = getattr(p, "stripe_checkout_session_id", None)
    if session_id and stored and session_id != stored:
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
