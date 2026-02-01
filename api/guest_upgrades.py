from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import and_, exists
from datetime import datetime, date, time
from typing import List, Optional

from database import get_db
from models import ChatSession, Reservation, Upgrade, UpgradePurchase

from app.services.upgrade_rules import (
    StayContext,
    UpgradeCtx,
    evaluate_upgrade,
)

router = APIRouter(prefix="/guest", tags=["guest-upgrades"])


# -----------------------------
# Helpers: parsing
# -----------------------------

def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _parse_time_loose(s: Optional[str], default: time) -> time:
    """
    Handles:
      - "15:00"
      - "4:00 PM"
      - "10:00 AM"
    """
    if not s:
        return default

    raw = str(s).strip()
    # try HH:MM (24h)
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).time()
        except Exception:
            pass

    # try 12h
    for fmt in ("%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(raw.upper(), fmt).time()
        except Exception:
            pass

    return default


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


# -----------------------------
# Response models
# -----------------------------

class EvaluatedUpgradeOut(BaseModel):
    id: int
    eligible: bool
    disabled_reason: str = ""
    opens_at: Optional[str] = None  # ISO
    title: str
    slug: str
    price_cents: int


class EvaluatedUpgradesOut(BaseModel):
    property_id: int
    session_id: int
    upgrades: List[EvaluatedUpgradeOut]


class CheckoutIn(BaseModel):
    session_id: Optional[int] = None


class CheckoutOut(BaseModel):
    checkout_url: str


# -----------------------------
# Repo-ish helpers
# -----------------------------

def get_session_or_401(db: Session, session_id: Optional[int]) -> ChatSession:
    if not session_id:
        raise HTTPException(status_code=401, detail="Missing session_id. Please unlock your stay first.")

    sess = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not sess or not sess.is_verified:
        raise HTTPException(status_code=401, detail="Session not verified. Please unlock your stay first.")

    if not sess.property_id:
        raise HTTPException(status_code=400, detail="Session missing property_id.")

    return sess


def get_stay_context(db: Session, property_id: int, session: ChatSession) -> StayContext:
    # Prefer dates stored on chat_sessions (your verify flow populates these)
    arrival = _parse_date(session.arrival_date)
    departure = _parse_date(session.departure_date)

    # If not present, try to look up Reservation by pms_reservation_id (if you store it)
    if (not arrival or not departure) and session.pms_reservation_id:
        r = (
            db.query(Reservation)
            .filter(
                Reservation.property_id == property_id,
                Reservation.pms_reservation_id == session.pms_reservation_id,
            )
            .first()
        )
        if r:
            arrival = arrival or r.arrival_date
            departure = departure or r.departure_date

    if not arrival or not departure:
        raise HTTPException(status_code=400, detail="Missing arrival/departure for this session.")

    # Times: prefer ChatSession’s strings if you store them (you currently store times elsewhere),
    # fall back to Reservation times, then default.
    default_checkin = time(16, 0)   # 4:00 PM
    default_checkout = time(10, 0)  # 10:00 AM

    checkin_t = default_checkin
    checkout_t = default_checkout

    # If you later store session.checkin_time / session.checkout_time, parse here.
    # For now, try Reservation record for same stay if available:
    stay_res = None
    if session.pms_reservation_id:
        stay_res = (
            db.query(Reservation)
            .filter(
                Reservation.property_id == property_id,
                Reservation.pms_reservation_id == session.pms_reservation_id,
            )
            .first()
        )

    if stay_res:
        checkin_t = _parse_time_loose(stay_res.checkin_time, default_checkin)
        checkout_t = _parse_time_loose(stay_res.checkout_time, default_checkout)

    # --- Turnover checks ---
    # Arrival-day turnover: someone else DEPARTS on arrival date
    has_arrival_turnover = db.query(
        exists().where(
            and_(
                Reservation.property_id == property_id,
                Reservation.departure_date == arrival,
                # exclude same reservation if we can
                *( [Reservation.pms_reservation_id != session.pms_reservation_id] if session.pms_reservation_id else [] )
            )
        )
    ).scalar()

    # Departure-day turnover: someone else ARRIVES on departure date
    has_departure_turnover = db.query(
        exists().where(
            and_(
                Reservation.property_id == property_id,
                Reservation.arrival_date == departure,
                *( [Reservation.pms_reservation_id != session.pms_reservation_id] if session.pms_reservation_id else [] )
            )
        )
    ).scalar()

    return StayContext(
        property_id=property_id,
        session_id=session.id,
        arrival_date=arrival,
        departure_date=departure,
        checkin_time=checkin_t,
        checkout_time=checkout_t,
        has_same_day_turnover_on_arrival=bool(has_arrival_turnover),
        has_same_day_turnover_on_departure=bool(has_departure_turnover),
    )


def get_upgrade_or_404(db: Session, upgrade_id: int) -> Upgrade:
    up = db.query(Upgrade).filter(Upgrade.id == upgrade_id).first()
    if not up:
        raise HTTPException(status_code=404, detail="Upgrade not found.")
    return up


def upgrade_to_ctx(up: Upgrade) -> UpgradeCtx:
    return UpgradeCtx(
        id=up.id,
        property_id=up.property_id,
        slug=up.slug,
        title=up.title,
        price_cents=up.price_cents,
        is_active=bool(up.is_active),
    )


def ensure_not_already_paid(db: Session, session_id: int, upgrade_id: int):
    paid = (
        db.query(UpgradePurchase)
        .filter(
            UpgradePurchase.guest_session_id == session_id,
            UpgradePurchase.upgrade_id == upgrade_id,
            UpgradePurchase.status == "paid",
        )
        .first()
    )
    if paid:
        raise HTTPException(status_code=409, detail="Upgrade already purchased for this stay.")


# -----------------------------
# TODO: Stripe integration hook
# -----------------------------
def create_stripe_checkout_url(*, db: Session, session: ChatSession, upgrade: Upgrade) -> str:
    """
    IMPORTANT: Replace internals with YOUR existing Stripe checkout creation.
    You already have an endpoint returning {checkout_url}. Reuse that logic here.

    Return: checkout_url (string)
    """
    raise NotImplementedError("Wire this to your existing Stripe checkout creation logic.")


# -----------------------------
# Endpoints
# -----------------------------

@router.get("/properties/{property_id}/upgrades/evaluated", response_model=EvaluatedUpgradesOut)
def get_evaluated_upgrades(property_id: int, session_id: int, db: Session = Depends(get_db)):
    session = get_session_or_401(db, session_id)

    if session.property_id != property_id:
        raise HTTPException(status_code=403, detail="Session does not belong to this property.")

    stay = get_stay_context(db, property_id, session)

    upgrades = (
        db.query(Upgrade)
        .filter(Upgrade.property_id == property_id, Upgrade.is_active == True)  # noqa: E712
        .order_by(Upgrade.sort_order.asc(), Upgrade.id.asc())
        .all()
    )

    out: List[EvaluatedUpgradeOut] = []
    for up in upgrades:
        result = evaluate_upgrade(upgrade=upgrade_to_ctx(up), stay=stay)
        out.append(
            EvaluatedUpgradeOut(
                id=up.id,
                eligible=result.eligible,
                disabled_reason=result.reason or "",
                opens_at=_iso(result.opens_at),
                title=up.title,
                slug=up.slug,
                price_cents=up.price_cents,
            )
        )

    return EvaluatedUpgradesOut(property_id=property_id, session_id=session.id, upgrades=out)


@router.post("/upgrades/{upgrade_id}/checkout", response_model=CheckoutOut)
def start_upgrade_checkout(upgrade_id: int, body: CheckoutIn, db: Session = Depends(get_db)):
    session = get_session_or_401(db, body.session_id)

    upgrade = get_upgrade_or_404(db, upgrade_id)

    if upgrade.property_id != session.property_id:
        raise HTTPException(status_code=403, detail="Upgrade does not belong to this property.")

    ensure_not_already_paid(db, session.id, upgrade.id)

    stay = get_stay_context(db, session.property_id, session)

    # ✅ enterprise-safe enforcement
    result = evaluate_upgrade(upgrade=upgrade_to_ctx(upgrade), stay=stay)
    if not result.eligible:
        raise HTTPException(status_code=403, detail=result.reason or "Upgrade not available.")

    # ✅ create Stripe checkout URL using your existing logic
    checkout_url = create_stripe_checkout_url(db=db, session=session, upgrade=upgrade)
    return CheckoutOut(checkout_url=checkout_url)
