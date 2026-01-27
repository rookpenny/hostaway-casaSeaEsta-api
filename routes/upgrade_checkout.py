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

def _require():
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")
    if not APP_BASE_URL:
        raise HTTPException(status_code=500, detail="Missing APP_BASE_URL")


@router.post("/guest/properties/{property_id}/upgrades/{upgrade_id}/checkout")
def create_upgrade_checkout(
    property_id: int,
    upgrade_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    _require()
    stripe.api_key = STRIPE_SECRET_KEY

    # Require guest unlock
    if not request.session.get(f"guest_verified_{property_id}", False):
        raise HTTPException(status_code=403, detail="Please unlock your stay before purchasing upgrades.")

    # Bind to the guest chat session (your “stay session”)
    guest_session_id = request.session.get(f"guest_session_{property_id}", None)

    upgrade = (
        db.query(Upgrade)
        .filter(
            Upgrade.id == upgrade_id,
            Upgrade.property_id == property_id,
            Upgrade.is_active == True,
        )
        .first()
    )
    if not upgrade:
        raise HTTPException(status_code=404, detail="Upgrade not found")

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    pmc_id = prop.pmc_id

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
        raise HTTPException(status_code=403, detail="This property is not accepting upgrade payments yet.")

    # ✅ Prevent repurchase: if a PAID purchase exists for this stay+upgrade, block it
    q = db.query(UpgradePurchase).filter(
        UpgradePurchase.property_id == property_id,
        UpgradePurchase.upgrade_id == upgrade_id,
        UpgradePurchase.status == "paid",
    )

    # If your UpgradePurchase has a guest_session_id column, scope it to the guest session:
    if guest_session_id is not None and hasattr(UpgradePurchase, "guest_session_id"):
        q = q.filter(UpgradePurchase.guest_session_id == int(guest_session_id))

    existing_paid = q.first()
    if existing_paid:
        raise HTTPException(
            status_code=409,
            detail="This upgrade has already been purchased for this stay.",
        )

    # Amount + platform fee
    amount = int(upgrade.price_cents or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid upgrade amount.")

    pct_fee = int(round(amount * 0.02))
    flat_fee = 30
    platform_fee = max(0, pct_fee + flat_fee)
    if platform_fee >= amount:
        platform_fee = max(0, amount - 1)

    # Create purchase (pending)
    purchase = UpgradePurchase(
        pmc_id=pmc_id,
        property_id=prop.id,
        upgrade_id=upgrade.id,
        amount_cents=amount,
        platform_fee_cents=platform_fee,
        status="pending",
    )

    if guest_session_id is not None and hasattr(purchase, "guest_session_id"):
        purchase.guest_session_id = int(guest_session_id)

    purchase.stripe_destination_account_id = integ.account_id
    purchase.net_amount_cents = amount - platform_fee

    if purchase.net_amount_cents <= 0:
        raise HTTPException(status_code=400, detail="Invalid upgrade pricing configuration.")

    db.add(purchase)
    db.commit()
    db.refresh(purchase)

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
                "currency": (upgrade.currency or "usd").lower(),
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

    return {"checkout_url": session.url, "purchase_id": purchase.id}


@router.get("/guest/properties/{property_id}/upgrades/paid")
def list_paid_upgrades_for_stay(
    property_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not request.session.get(f"guest_verified_{property_id}", False):
        raise HTTPException(status_code=403, detail="Please unlock your stay first.")

    guest_session_id = request.session.get(f"guest_session_{property_id}", None)

    q = db.query(UpgradePurchase).filter(
        UpgradePurchase.property_id == property_id,
        UpgradePurchase.status == "paid",
    )

    if guest_session_id is not None and hasattr(UpgradePurchase, "guest_session_id"):
        q = q.filter(UpgradePurchase.guest_session_id == int(guest_session_id))

    rows = q.all()
    return {
        "paid_upgrade_ids": sorted({int(r.upgrade_id) for r in rows if r.upgrade_id is not None})
    }


@router.get("/guest/upgrades/purchase-status")
def upgrade_purchase_status(
    purchase_id: int = Query(...),
    session_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    p = db.query(UpgradePurchase).filter(UpgradePurchase.id == purchase_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Purchase not found")

    if session_id and getattr(p, "stripe_checkout_session_id", None):
        if session_id != p.stripe_checkout_session_id:
            raise HTTPException(status_code=403, detail="Invalid session for purchase")

    status = (getattr(p, "status", "") or "pending").lower()
    return {
        "purchase_id": p.id,
        "status": status,
        "upgrade_id": getattr(p, "upgrade_id", None),
        "property_id": getattr(p, "property_id", None),
        "paid": status == "paid",
        "refunded": status == "refunded",
    }
