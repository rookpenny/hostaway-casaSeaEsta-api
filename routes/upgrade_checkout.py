# routes/upgrade_checkout.py
import os
from typing import Optional, Dict, Any
from datetime import datetime, date, timedelta

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from database import get_db
from models import PMCIntegration, Property, Upgrade, UpgradePurchase, ChatSession, Reservation

from utils.upgrades_eligibility import is_upgrade_eligible

router = APIRouter()


# -------------------------
# Helpers
# -------------------------
def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _require_env() -> tuple[str, str]:
    stripe_secret = _env("STRIPE_SECRET_KEY")
    app_base = (_env("APP_BASE_URL")).rstrip("/")

    missing = []
    if not stripe_secret:
        missing.append("STRIPE_SECRET_KEY")
    if not app_base:
        missing.append("APP_BASE_URL")

    if missing:
        raise HTTPException(status_code=500, detail=f"Missing env vars: {', '.join(missing)}")

    return stripe_secret, app_base

async def _resolve_guest_session_id(request: Request, property_id: int) -> int:
    # 1) cookie session
    raw = request.session.get(f"guest_session_{property_id}")
    if raw is not None:
        try:
            return int(raw)
        except Exception:
            pass

    # 2) query string fallback
    qp = request.query_params.get("guest_session_id") or request.query_params.get("session_id")
    if qp:
        try:
            return int(qp)
        except Exception:
            pass

    # 3) JSON body fallback (if present)
    try:
        data = await request.json()
        if isinstance(data, dict):
            v = data.get("guest_session_id") or data.get("session_id")
            if v is not None:
                return int(v)
    except Exception:
        pass

    raise HTTPException(status_code=400, detail="Missing guest session for this stay.")


def _parse_ymd(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def _get_stay_reservation(db: Session, *, property_id: int, guest_session_id: int) -> Optional[Reservation]:
    """
    Resolve the guest's Reservation row using ChatSession stay dates.
    Prefer reservation_id when present, then phone_last4, then dates-only.
    """
    sess = db.query(ChatSession).filter(ChatSession.id == int(guest_session_id)).first()
    if not sess:
        return None

    # ✅ 0) Try reservation_id first (most reliable)
    rid = (getattr(sess, "reservation_id", None) or "").strip()
    if rid and hasattr(Reservation, "reservation_id"):
        hit = (
            db.query(Reservation)
            .filter(
                Reservation.property_id == int(property_id),
                Reservation.reservation_id == rid,
            )
            .order_by(Reservation.id.desc())
            .first()
        )
        if hit:
            return hit

    # ✅ 1) Fallback to arrival/departure
    arr = _parse_ymd(getattr(sess, "arrival_date", None))
    dep = _parse_ymd(getattr(sess, "departure_date", None))
    if not arr or not dep:
        return None

    base_q = db.query(Reservation).filter(
        Reservation.property_id == int(property_id),
        Reservation.arrival_date == arr,
        Reservation.departure_date == dep,
    )

    # ✅ 2) Phone match if available, but don't fail if not populated
    phone_last4 = (getattr(sess, "phone_last4", None) or "").strip()
    if phone_last4 and hasattr(Reservation, "phone_last4"):
        hit = base_q.filter(Reservation.phone_last4 == phone_last4).order_by(Reservation.id.desc()).first()
        if hit:
            return hit

    return base_q.order_by(Reservation.id.desc()).first()

def _guest_verified(request: Request, property_id: int) -> bool:
    return bool(request.session.get(f"guest_verified_{property_id}", False))


def _require_guest_session_id(request: Request, property_id: int) -> int:
    raw = request.session.get(f"guest_session_{property_id}")
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
    fee = max(0, pct_fee + flat_fee)
    if fee >= amount_cents:
        fee = max(0, amount_cents - 1)
    return fee


async def _create_checkout_for_upgrade(
    db: Session,
    request: Request,
    *,
    property_id: int,
    upgrade: Upgrade,
) -> Dict[str, Any]:
    stripe_secret, app_base_url = _require_env()
    _require_model_has_guest_session_id()

    stripe.api_key = stripe_secret

    if not _guest_verified(request, property_id):
        raise HTTPException(status_code=403, detail="Please unlock your stay before purchasing upgrades.")

    #guest_session_id = _require_guest_session_id(request, property_id)
    guest_session_id = await _resolve_guest_session_id(request, property_id)

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    # Ensure upgrade belongs to this property
    if int(getattr(upgrade, "property_id", 0) or 0) != int(property_id):
        raise HTTPException(status_code=404, detail="Upgrade not found")

    # ✅ Eligibility gate (same-day turnover + timing rules)
    reservation = _get_stay_reservation(db, property_id=int(property_id), guest_session_id=int(guest_session_id))
    if not reservation:
        raise HTTPException(status_code=400, detail="Could not find your stay details for this upgrade.")

    eligible, reason = is_upgrade_eligible(db=db, upgrade=upgrade, reservation=reservation)
    if not eligible:
        raise HTTPException(status_code=403, detail=reason)


    pmc_id = int(prop.pmc_id)
    destination_account_id = _get_stripe_destination_account(db, pmc_id)

    _prevent_repurchase(
        db,
        property_id=int(property_id),
        upgrade_id=int(upgrade.id),
        guest_session_id=int(guest_session_id),
    )

    amount = int(getattr(upgrade, "price_cents", 0) or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid upgrade amount.")

    platform_fee = _calc_platform_fee(amount)
    net_amount = amount - platform_fee
    if net_amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid upgrade pricing configuration.")

    currency = (getattr(upgrade, "currency", None) or "usd").lower().strip()
    title = getattr(upgrade, "title", None) or "Upgrade"

    # Create the purchase first so we have purchase_id for metadata
    purchase = UpgradePurchase(
        pmc_id=pmc_id,
        property_id=int(prop.id),
        upgrade_id=int(upgrade.id),
        guest_session_id=int(guest_session_id),
        amount_cents=int(amount),
        platform_fee_cents=int(platform_fee),
        net_amount_cents=int(net_amount),
        currency=currency,
        status="pending",
        stripe_destination_account_id=destination_account_id,
    )

    db.add(purchase)
    db.commit()
    db.refresh(purchase)

    success_url = (
        f"{app_base_url}/guest/{prop.id}"
        f"?screen=upgrades"
        f"&upgrade=success"
        f"&purchase_id={purchase.id}"
        f"&upgrade_id={upgrade.id}"
        f"&session_id={{CHECKOUT_SESSION_ID}}"
    )
    cancel_url = (
        f"{app_base_url}/guest/{prop.id}"
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
# POST /guest/upgrades/{upgrade_id}/checkout
# ==========================================================
@router.post("/guest/upgrades/{upgrade_id}/checkout")
def create_upgrade_checkout_guest(
    upgrade_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    upgrade = (
        db.query(Upgrade)
        .filter(Upgrade.id == upgrade_id, Upgrade.is_active == True)  # noqa: E712
        .first()
    )
    if not upgrade:
        raise HTTPException(status_code=404, detail="Upgrade not found")

    return _create_checkout_for_upgrade(db, request, property_id=int(upgrade.property_id), upgrade=upgrade)


# ==========================================================
# POST /guest/properties/{property_id}/upgrades/{upgrade_id}/checkout
# ==========================================================
@router.post("/guest/properties/{property_id}/upgrades/{upgrade_id}/checkout")
async def create_upgrade_checkout(
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

    return await _create_checkout_for_upgrade(db, request, property_id=int(property_id), upgrade=upgrade)


# ==========================================================
# GET /guest/properties/{property_id}/upgrades/paid
# ==========================================================
@router.get("/guest/properties/{property_id}/upgrades/paid")
def list_paid_upgrades_for_stay(
    property_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_model_has_guest_session_id()

    if not _guest_verified(request, property_id):
        raise HTTPException(status_code=403, detail="Please unlock your stay first.")

    guest_session_id = _require_guest_session_id(request, property_id)

    rows = (
        db.query(UpgradePurchase)
        .filter(
            UpgradePurchase.property_id == int(property_id),
            UpgradePurchase.guest_session_id == int(guest_session_id),
            UpgradePurchase.status == "paid",
        )
        .all()
    )

    return {"paid_upgrade_ids": sorted({int(r.upgrade_id) for r in rows if r.upgrade_id is not None})}


# ==========================================================
# GET /guest/upgrades/purchase-status
# ==========================================================
@router.get("/guest/upgrades/purchase-status")
def upgrade_purchase_status(
    purchase_id: int = Query(...),
    session_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    p = db.query(UpgradePurchase).filter(UpgradePurchase.id == int(purchase_id)).first()
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
