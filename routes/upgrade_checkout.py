# routes/upgrade_checkout.py
import os
from typing import Optional, Dict, Any
from datetime import datetime, date

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Body
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from models import PMCIntegration, Property, Upgrade, UpgradePurchase, ChatSession, Reservation
from utils.upgrades_eligibility import is_upgrade_eligible
from utils.pmc_messages import upsert_pmc_message



router = APIRouter()

class CheckoutBody(BaseModel):
    session_id: Optional[int] = None


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


def _parse_ymd(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def _guest_verified(request: Request, property_id: int) -> bool:
    return bool(request.session.get(f"guest_verified_{property_id}", False))


def _require_guest_session_id_from_cookie(request: Request, property_id: int) -> int:
    raw = request.session.get(f"guest_session_{property_id}")
    if raw is None:
        raise HTTPException(status_code=400, detail="Missing guest session for this stay.")
    try:
        return int(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid guest session for this stay.")



def _resolve_guest_session_id(
    request: Request,
    property_id: int,
    *,
    session_id_q: Optional[int],
    session_id_body: Optional[int],
) -> int:
    """
    Resolve guest_session_id in priority order:
      1) query param (?session_id=)
      2) JSON body { session_id }
      3) cookie-backed session (guest_session_{property_id})
    """
    if session_id_q is not None:
        try:
            return int(session_id_q)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid session_id (query).")

    if session_id_body is not None:
        try:
            return int(session_id_body)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid session_id (body).")

    return _require_guest_session_id(request, property_id)


def _extract_guest_session_id(
    request: Request,
    property_id: int,
    session_id_query: Optional[int],
    body: Dict[str, Any],
) -> int:
    """
    Accept session_id from:
      1) query param ?session_id=
      2) JSON body { session_id: ... }
      3) cookie-backed server session guest_session_{property_id}
    """
    # 1) query param
    if session_id_query is not None:
        try:
            return int(session_id_query)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid session_id")

    # 2) JSON body
    if isinstance(body, dict) and body.get("session_id") is not None:
        try:
            return int(body.get("session_id"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid session_id")

    # 3) cookie-backed session
    return _require_guest_session_id_from_cookie(request, property_id)


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


def _get_stay_reservation(
    db: Session,
    *,
    property_id: int,
    guest_session_id: int,
) -> Optional[Reservation]:
    """
    Resolve the guest's Reservation row using ChatSession stay details.

    Priority:
      1) ChatSession.reservation_id -> Reservation.pms_reservation_id
      2) arrival_date + departure_date (+ phone_last4 if available)
      3) If nothing exists in DB, return an in-memory Reservation built from ChatSession
         so eligibility logic can still run.
    """
    sess = db.query(ChatSession).filter(ChatSession.id == int(guest_session_id)).first()
    if not sess:
        return None

    arr = _parse_ymd(getattr(sess, "arrival_date", None))
    dep = _parse_ymd(getattr(sess, "departure_date", None))
    if not arr or not dep:
        return None

    # --- 1) Reservation-id match (Hostaway) ---
    cs_res_id = (getattr(sess, "reservation_id", None) or "").strip()
    if cs_res_id and hasattr(Reservation, "pms_reservation_id"):
        hit = (
            db.query(Reservation)
            .filter(
                Reservation.property_id == int(property_id),
                Reservation.pms_reservation_id == cs_res_id,
            )
            .order_by(Reservation.id.desc())
            .first()
        )
        if hit:
            return hit

    # --- 2) Date match (with best-effort phone_last4) ---
    base_q = db.query(Reservation).filter(
        Reservation.property_id == int(property_id),
        Reservation.arrival_date == arr,
        Reservation.departure_date == dep,
    )

    phone_last4 = (getattr(sess, "phone_last4", None) or "").strip()
    has_phone_col = hasattr(Reservation, "phone_last4")

    if phone_last4 and has_phone_col:
        hit = (
            base_q.filter(Reservation.phone_last4 == phone_last4)
            .order_by(Reservation.id.desc())
            .first()
        )
        if hit:
            return hit

    hit = base_q.order_by(Reservation.id.desc()).first()
    if hit:
        return hit

    # --- 3) Fallback: build an in-memory Reservation so eligibility can run ---
    ghost = Reservation(
        property_id=int(property_id),
        arrival_date=arr,
        departure_date=dep,
    )
    if hasattr(ghost, "phone_last4"):
        ghost.phone_last4 = phone_last4 or None
    if hasattr(ghost, "guest_name"):
        ghost.guest_name = (getattr(sess, "guest_name", None) or None)

    # Optional: carry pms id for debugging
    if hasattr(ghost, "pms_reservation_id") and cs_res_id:
        ghost.pms_reservation_id = cs_res_id

    return ghost


def _create_checkout_for_upgrade(
    db: Session,
    request: Request,
    *,
    property_id: int,
    upgrade: Upgrade,
    guest_session_id: int,
) -> Dict[str, Any]:
    stripe_secret, app_base_url = _require_env()
    _require_model_has_guest_session_id()

    stripe.api_key = stripe_secret

    if not _guest_verified(request, property_id):
        raise HTTPException(status_code=403, detail="Please unlock your stay before purchasing upgrades.")

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    # Ensure upgrade belongs to this property
    if int(getattr(upgrade, "property_id", 0) or 0) != int(property_id):
        raise HTTPException(status_code=404, detail="Upgrade not found")

    # ✅ Eligibility gate (now robust)
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

    upsert_pmc_message(
        db,
        pmc_id=int(pmc_id),
        dedupe_key=f"upgrade_purchase:pending:{int(purchase.id)}",
        msg_type="upgrade_purchase_pending",
        subject=f"Upgrade checkout started: {title}",
        body=f"A guest started checkout for {title}. Purchase ID: {purchase.id}",
        severity="info",
        status="open",
        purchase=purchase,
    )

    db.add(purchase)
    db.commit()
    db.refresh(purchase)


        # --- PMC inbox message: pending purchase started ---
    upsert_pmc_message(
        db,
        pmc_id=int(pmc_id),
        dedupe_key=f"upgrade_purchase:pending:{purchase.id}",
        type="upgrade_purchase_pending",
        severity="info",
        status="open",
        subject=f"Upgrade started: {title}",
        body=(
            f"A guest started checkout for **{title}**.\n\n"
            f"- Amount: {amount/100:.2f} {currency.upper()}\n"
            f"- Status: pending\n"
            f"- Purchase ID: {purchase.id}\n"
        ),
        property_id=int(prop.id),
        upgrade_purchase_id=int(purchase.id),
        upgrade_id=int(upgrade.id),
        guest_session_id=int(guest_session_id),
        link_url=f"/admin/dashboard?view=upgrades",  # or a better link you have
    )
    db.commit()


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
# POST /guest/properties/{property_id}/upgrades/{upgrade_id}/checkout
# ==========================================================
@router.post("/guest/properties/{property_id}/upgrades/{upgrade_id}/checkout")
def create_upgrade_checkout(
    property_id: int,
    upgrade_id: int,
    request: Request,
    payload: Optional[CheckoutBody] = None,
    session_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    # --- load upgrade ---
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

    # --- resolve the CORRECT ChatSession.id ---
    guest_session_id = _resolve_guest_session_id(
        request,
        property_id,
        session_id_q=session_id,
        session_id_body=(payload.session_id if payload else None),
    )

    # --- delegate to your existing checkout logic ---
    return _create_checkout_for_upgrade(
        db,
        request,
        property_id=int(property_id),
        upgrade=upgrade,
        guest_session_id=int(guest_session_id),
    )

# ==========================================================
# POST /guest/upgrades/{upgrade_id}/checkout  (optional keep)
# ==========================================================
@router.post("/guest/upgrades/{upgrade_id}/checkout")
def create_upgrade_checkout_guest(
    upgrade_id: int,
    request: Request,
    db: Session = Depends(get_db),
    session_id: Optional[int] = Query(default=None),
    body: Dict[str, Any] = Body(default={}),
):
    upgrade = (
        db.query(Upgrade)
        .filter(Upgrade.id == upgrade_id, Upgrade.is_active == True)  # noqa: E712
        .first()
    )
    if not upgrade:
        raise HTTPException(status_code=404, detail="Upgrade not found")

    guest_session_id = _extract_guest_session_id(request, int(upgrade.property_id), session_id, body)

    return _create_checkout_for_upgrade(
        db,
        request,
        property_id=int(upgrade.property_id),
        upgrade=upgrade,
        guest_session_id=int(guest_session_id),
    )


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

    guest_session_id = _require_guest_session_id_from_cookie(request, property_id)

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
