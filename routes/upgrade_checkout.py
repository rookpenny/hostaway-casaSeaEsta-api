# routes/upgrade_checkout.py
import os
from typing import Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from database import get_db
from models import PMCIntegration, Property, Upgrade, UpgradePurchase

router = APIRouter()

STRIPE_SECRET_KEY = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").rstrip("/")


# -------------------------
# Helpers
# -------------------------
def _require_env() -> None:
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")
    if not APP_BASE_URL:
        raise HTTPException(status_code=500, detail="Missing APP_BASE_URL")


def _guest_verified(request: Request, property_id: int) -> bool:
    return bool(request.session.get(f"guest_verified_{property_id}", False))


def _guest_session_id_required(request: Request, property_id: int) -> int:
    """
    Option A: purchases MUST be scoped to a stay (guest_session_id).
    If this is missing, we don't allow checkout creation.
    """
    raw = request.session.get(f"guest_session_{property_id}", None)
    if raw is None:
        raise HTTPException(status_code=400, detail="Missing guest session for this stay.")
    try:
        return int(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid guest session for this stay.")


def _require_model_has_guest_session_id() -> None:
    if not hasattr(UpgradePurchase, "guest_session_id"):
        raise HTTPException(
            status_code=500,
            detail="UpgradePurchase is missing guest_session_id. Add the column to your model and database.",
        )


def _get_stripe_destination_account(db: Session, pmc_id: int) -> str:
    integ = (
        db.query(PMCIntegration)
        .filter(
            PMCIntegration.pmc_id == pmc_id,
            PMCIntegration.provider == "stripe_connect",
            PMCIntegration.is_connected == True,  # noqa: E712
        )
        .first()
    )
    if not integ or not getattr(integ, "account_id", None):
        raise HTTPException(
            status_code=403,
            detail="This property is not accepting upgrade payments yet.",
        )
    return str(integ.account_id)


def _prevent_repurchase(db: Session, *, property_id: int, upgrade_id: int, guest_session_id: int) -> None:
    """
    Block repurchase ONLY for the same stay (guest_session_id).
    """
    existing = (
        db.query(UpgradePurchase)
        .filter(
            UpgradePurchase.property_id == property_id,
            UpgradePurchase.upgrade_id == upgrade_id,
            UpgradePurchase.guest_session_id == guest_session_id,
            UpgradePurchase.status == "paid",
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="This upgrade has already been purchased for this stay.")


def _calc_platform_fee(amount_cents: int) -> int:
    pct_fee = int(round(amount_cents * 0.02))
    flat_fee = 30
    platform_fee = max(0, pct_fee + flat_fee)
    # never allow fee >= amount
    if platform_fee >= amount_cents:
        platform_fee = max(0, amount_cents - 1)
    return platform_fee


def _create_checkout_for_upgrade(
    db: Session,
    request: Request,
    *,
    property_id: int,
    upgrade: Upgrade,
) -> dict:
    _require_env()
    _require_model_has_guest_session_id()

    stripe.api_key = STRIPE_SECRET_KEY

    if not _guest_verified(request, property_id):
        raise HTTPException(status_code=403, detail="Please unlock your stay before purchasing upgrades.")

    guest_session_id = _guest_session_id_required(request, property_id)

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    pmc_id = int(prop.pmc_id)
    destination_account_id = _get_stripe_destination_account(db, pmc_id)

    # Ensure upgrade belongs to this property
    if int(getattr(upgrade, "property_id", 0) or 0) != int(property_id):
        raise HTTPException(status_code=404, detail="Upgrade not found")

    # Prevent repurchase (paid) for this stay
    _prevent_repurchase(db, property_id=property_id, upgrade_id=int(upgrade.id), guest_session_id=guest_session_id)

    # Amount
    amount = int(getattr(upgrade, "price_cents", 0) or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid upgrade amount.")

    platform_fee = _calc_platform_fee(amount)
    net_amount = amount - platform_fee
    if net_amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid upgrade pricing configuration.")

    currency = (getattr(upgrade, "currency", None) or "usd").lower().strip()
    title = getattr(upgrade, "title", None) or "Upgrade"

    # Create purchase row first (pending) so we have purchase_id for metadata/urls
    purchase = UpgradePurchase(
        pmc_id=pmc_id,
        property_id=int(prop.id),
        upgrade_id=int(upgrade.id),
        guest_session_id=guest_session_id,
        amount_cents=amount,
        platform_fee_cents=platform_fee,
        net_amount_cents=net_amount,
        currency=currency,
        status="pending",
        stripe_destination_account_id=destination_account_id,
    )

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

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": currency,
                        "product_data": {"name": title},
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
                "guest_session_id": str(guest_session_id),
            },
            payment_intent_data={
                "application_fee_amount": platform_fee,
                "transfer_data": {"destination": destination_account_id},
                "metadata": {
                    "type": "upgrade_purchase",
                    "purchase_id": str(purchase.id),
                    "pmc_id": str(pmc_id),
                    "property_id": str(prop.id),
                    "upgrade_id": str(upgrade.id),
                    "guest_session_id": str(guest_session_id),
                },
            },
        )
    except Exception:
        # keep DB consistent if Stripe fails
        try:
            purchase.status = "failed"
            db.add(purchase)
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=502, detail="Failed to create Stripe checkout session.")

    purchase.stripe_checkout_session_id = session.id
    db.add(purchase)
    db.commit()

    return {"checkout_url": session.url, "purchase_id": purchase.id}


# ==========================================================
# OPTION A: POST /guest/upgrades/{upgrade_id}/checkout
# ==========================================================
@router.post("/guest/upgrades/{upgrade_id}/checkout")
def create_upgrade_checkout_guest(
    upgrade_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    upgrade = db.query(Upgrade).filter(Upgrade.id == upgrade_id, Upgrade.is_active == True).first()  # noqa: E712
    if not upgrade:
        raise HTTPException(status_code=404, detail="Upgrade not found")

    property_id = int(upgrade.property_id)
    return _create_checkout_for_upgrade(db, request, property_id=property_id, upgrade=upgrade)


# ==========================================================
# Property-scoped checkout route (optional)
# ==========================================================
@router.post("/guest/properties/{property_id}/upgrades/{upgrade_id}/checkout")
def create_upgrade_checkout(
    property_id: int,
    upgrade_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    upgrade = (
        db.query(Upgrade)
        .filter(
            Upgrade.id == upgrade_id,
            Upgrade.property_id == property_id,
            Upgrade.is_active == True,  # noqa: E712
        )
        .first()
    )
    if not upgrade:
        raise HTTPException(status_code=404, detail="Upgrade not found")

    return _create_checkout_for_upgrade(db, request, property_id=property_id, upgrade=upgrade)


@router.get("/guest/properties/{property_id}/upgrades/paid")
def list_paid_upgrades_for_stay(
    property_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_model_has_guest_session_id()

    if not _guest_verified(request, property_id):
        raise HTTPException(status_code=403, detail="Please unlock your stay first.")

    guest_session_id = _guest_session_id_required(request, property_id)

    rows = (
        db.query(UpgradePurchase)
        .filter(
            UpgradePurchase.property_id == property_id,
            UpgradePurchase.guest_session_id == guest_session_id,
            UpgradePurchase.status == "paid",
        )
        .all()
    )

    return {"paid_upgrade_ids": sorted({int(r.upgrade_id) for r in rows if r.upgrade_id is not None})}


@router.get("/guest/upgrades/purchase-status")
def upgrade_purchase_status(
    purchase_id: int = Query(...),
    session_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    p = db.query(UpgradePurchase).filter(UpgradePurchase.id == purchase_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Purchase not found")

    if session_id and getattr(p, "stripe_checkout_session_id", None):
        if session_id != p.stripe_checkout_session_id:
            raise HTTPException(status_code=403, detail="Invalid session for purchase")

    status_str = (getattr(p, "status", "") or "pending").lower()
    return {
        "purchase_id": p.id,
        "status": status_str,
        "upgrade_id": getattr(p, "upgrade_id", None),
        "property_id": getattr(p, "property_id", None),
        "paid": status_str == "paid",
        "refunded": status_str == "refunded",
    }
