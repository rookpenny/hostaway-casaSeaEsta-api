# routes/upgrade_checkout.py
import os
import logging
import stripe

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from database import get_db
from models import Upgrade, Property, PMCIntegration, UpgradePurchase

logger = logging.getLogger("uvicorn.error")
router = APIRouter()

STRIPE_SECRET_KEY = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").rstrip("/")


def _require_stripe() -> None:
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Missing STRIPE_SECRET_KEY")
    stripe.api_key = STRIPE_SECRET_KEY


def _get_app_base_url(request: Request | None) -> str:
    """
    Prefer APP_BASE_URL if set (recommended on Render).
    Fallback to request.base_url if needed.
    """
    if APP_BASE_URL:
        return APP_BASE_URL
    if request is not None:
        return str(request.base_url).rstrip("/")
    raise HTTPException(status_code=500, detail="Missing APP_BASE_URL")


def _get_upgrade_and_property(
    db: Session, property_id: int, upgrade_id: int
) -> tuple[Upgrade, Property]:
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

    return upgrade, prop


def _get_connected_account(db: Session, pmc_id: int) -> str | None:
    """
    Returns Stripe Connect account_id if connected, else None.
    """
    integ = (
        db.query(PMCIntegration)
        .filter(
            PMCIntegration.pmc_id == pmc_id,
            PMCIntegration.provider == "stripe_connect",
            PMCIntegration.is_connected == True,
        )
        .first()
    )
    account_id = (getattr(integ, "account_id", None) or "").strip() if integ else ""
    return account_id or None


def _calc_platform_fee(amount_cents: int) -> int:
    # Example: 2% + $0.30
    pct_fee = int(round(amount_cents * 0.02))
    flat_fee = 30
    fee = max(0, pct_fee + flat_fee)

    # fee must be < amount for destination charges
    if fee >= amount_cents:
        fee = max(0, amount_cents - 1)
    return fee


def _create_checkout_session(
    *,
    request: Request | None,
    db: Session,
    prop: Property,
    upgrade: Upgrade,
) -> dict:
    _require_stripe()

    amount = int(getattr(upgrade, "price_cents", 0) or 0)
    currency = (getattr(upgrade, "currency", None) or "usd").lower().strip()
    title = (getattr(upgrade, "title", None) or "Upgrade").strip()

    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid upgrade amount.")

    app_base = _get_app_base_url(request)

    pmc_id = int(getattr(prop, "pmc_id", 0) or 0)
    destination_account = _get_connected_account(db, pmc_id)

    platform_fee = _calc_platform_fee(amount) if destination_account else 0

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

    # Store destination (if any)
    if destination_account:
        purchase.stripe_destination_account_id = destination_account
        purchase.net_amount_cents = amount - platform_fee
    else:
        purchase.stripe_destination_account_id = None
        purchase.net_amount_cents = amount

    db.commit()

    # Guest returns to /guest/{prop.id} and your JS reads these query params
    success_url = (
        f"{app_base}/guest/{prop.id}"
        f"?screen=upgrades"
        f"&upgrade=success"
        f"&purchase_id={purchase.id}"
        f"&upgrade_id={upgrade.id}"
        f"&session_id={{CHECKOUT_SESSION_ID}}"
    )

    cancel_url = (
        f"{app_base}/guest/{prop.id}"
        f"?screen=upgrades"
        f"&upgrade=cancel"
        f"&purchase_id={purchase.id}"
        f"&upgrade_id={upgrade.id}"
    )

    try:
        session_kwargs: dict = dict(
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
            },
        )

        # If PMC has a connected Stripe account, route funds there (destination charge)
        if destination_account:
            session_kwargs["payment_intent_data"] = {
                "application_fee_amount": platform_fee,
                "transfer_data": {"destination": destination_account},
                "metadata": {
                    "type": "upgrade_purchase",
                    "purchase_id": str(purchase.id),
                    "pmc_id": str(pmc_id),
                    "property_id": str(prop.id),
                    "upgrade_id": str(upgrade.id),
                },
            }

        session = stripe.checkout.Session.create(**session_kwargs)

    except Exception as e:
        logger.exception("Stripe checkout creation failed: %r", e)
        raise HTTPException(status_code=500, detail="Unable to start checkout for this upgrade.")

    purchase.stripe_checkout_session_id = session.id
    db.commit()

    return {"checkout_url": session.url, "purchase_id": purchase.id}


# ✅ THIS matches your frontend (guest_app.js)
@router.post("/properties/{property_id}/upgrades/{upgrade_id}/checkout")
def checkout_for_property(
    property_id: int,
    upgrade_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    # enforce unlock like your main.py route did
    if not request.session.get(f"guest_verified_{property_id}", False):
        raise HTTPException(status_code=403, detail="Please unlock your stay before purchasing upgrades.")

    upgrade, prop = _get_upgrade_and_property(db, property_id, upgrade_id)
    return _create_checkout_session(request=request, db=db, prop=prop, upgrade=upgrade)


# ✅ Keeps your old route working too (optional)
@router.post("/guest/upgrades/{upgrade_id}/checkout")
def checkout_guest_route(
    upgrade_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    # Find upgrade -> property
    upgrade = db.query(Upgrade).filter(Upgrade.id == upgrade_id, Upgrade.is_active == True).first()
    if not upgrade:
        raise HTTPException(status_code=404, detail="Upgrade not found")

    prop = db.query(Property).filter(Property.id == upgrade.property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    # Optional: you can enforce unlock here too if you want
    if not request.session.get(f"guest_verified_{prop.id}", False):
        raise HTTPException(status_code=403, detail="Please unlock your stay before purchasing upgrades.")

    return _create_checkout_session(request=request, db=db, prop=prop, upgrade=upgrade)


@router.get("/guest/upgrades/purchase-status")
def upgrade_purchase_status(
    purchase_id: int = Query(...),
    session_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    p = db.query(UpgradePurchase).filter(UpgradePurchase.id == purchase_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Purchase not found")

    # Optional safety check
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
