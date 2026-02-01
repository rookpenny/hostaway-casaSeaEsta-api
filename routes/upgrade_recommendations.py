# routes/upgrade_recommendations.py
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from typing import Optional, Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import Property, Upgrade, Reservation
from utils.upgrades_eligibility import is_upgrade_eligible

router = APIRouter()


# -------------------------
# Helpers
# -------------------------
def _guest_verified(request: Request, property_id: int) -> bool:
    return bool(request.session.get(f"guest_verified_{property_id}", False))


def _eligible_hour() -> int:
    """
    Hour of day (0-23) when an upgrade becomes "eligible" on the eligible date.
    Defaults to 9 AM server local time.
    """
    raw = (os.getenv("RECOMMENDATION_ELIGIBLE_HOUR") or "").strip()
    if not raw:
        return 9
    try:
        h = int(raw)
        if 0 <= h <= 23:
            return h
    except Exception:
        pass
    return 9


def _get_upcoming_or_current_reservation(
    db: Session,
    property_id: int,
    phone_last4: Optional[str],
) -> Optional[Reservation]:
    """
    Use the guest's phone_last4 from session if available (best),
    otherwise fallback to next reservation for the property.
    """
    today = date.today()

    q = db.query(Reservation).filter(Reservation.property_id == property_id)

    if phone_last4:
        q2 = q.filter(Reservation.phone_last4 == phone_last4).order_by(Reservation.arrival_date.asc())

        # prefer current stay, else next upcoming
        current = q2.filter(Reservation.arrival_date <= today, Reservation.departure_date >= today).first()
        if current:
            return current

        upcoming = q2.filter(Reservation.arrival_date >= today).first()
        if upcoming:
            return upcoming

    # fallback: first upcoming for property
    return (
        q.filter(Reservation.arrival_date >= today)
        .order_by(Reservation.arrival_date.asc())
        .first()
    )


def _format_date(d: date) -> str:
    return d.isoformat()


def _format_time_12h(dt: datetime) -> str:
    # Linux supports %-I, Windows may not. If you ever run on Windows, swap to %#I there.
    try:
        return dt.strftime("%-I:%M %p")
    except Exception:
        return dt.strftime("%I:%M %p").lstrip("0")


def _next_eligible_at(upgrade_slug: str, reservation: Reservation) -> Optional[datetime]:
    """
    Only for early-check-in / late-checkout.
    Returns the earliest datetime the upgrade *could* become eligible based on your preclear windows
    (turnover still re-checked at request-time).
    """
    arr = reservation.arrival_date
    dep = reservation.departure_date
    h = _eligible_hour()

    if upgrade_slug == "early-check-in":
        d = arr - timedelta(days=2)
        return datetime.combine(d, time(hour=h, minute=0))
    if upgrade_slug == "late-checkout":
        d = dep - timedelta(days=1)
        return datetime.combine(d, time(hour=h, minute=0))

    return None


# -------------------------
# Route
# -------------------------
@router.get("/guest/properties/{property_id}/upgrades/{upgrade_id}/recommendation")
def upgrade_recommendation(
    property_id: int,
    upgrade_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    if not _guest_verified(request, property_id):
        raise HTTPException(status_code=403, detail="Please unlock your stay first.")

    # Load property + upgrade
    prop = db.query(Property).filter(Property.id == int(property_id)).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    upgrade = (
        db.query(Upgrade)
        .filter(
            Upgrade.id == int(upgrade_id),
            Upgrade.property_id == int(property_id),
            Upgrade.is_active.is_(True),
        )
        .first()
    )
    if not upgrade:
        raise HTTPException(status_code=404, detail="Upgrade not found")

    # Find reservation for this guest (best: stash phone_last4 in session at verify)
    phone_last4 = None
    try:
        phone_last4 = request.session.get(f"guest_phone_last4_{property_id}")
    except Exception:
        phone_last4 = None

    reservation = _get_upcoming_or_current_reservation(
        db,
        property_id=int(property_id),
        phone_last4=phone_last4,
    )

    if not reservation:
        # If reservations aren’t synced, don’t hard-fail. Just return a helpful message.
        return {
            "eligible": False,
            "reason": "We can’t verify your stay dates yet, so we can’t confirm upgrade availability.",
            "next_eligible_date": None,
            "next_eligible_at": None,
            "suggested_message": "Try again later, or message your host for availability.",
            "reservation": None,
        }

    today = date.today()
    now = datetime.now()

    slug = (getattr(upgrade, "slug", "") or "").lower().strip()
    eligible, reason = is_upgrade_eligible(db=db, upgrade=upgrade, reservation=reservation, today=today)

    next_at = _next_eligible_at(slug, reservation)
    next_day = next_at.date() if next_at else None

    # Friendly recommendation copy (exact “eligible tomorrow at X”)
    if eligible:
        suggested = "✅ You’re eligible now."
    else:
        if next_at and now < next_at:
            if next_day == today + timedelta(days=1):
                suggested = f"You’ll be eligible tomorrow at {_format_time_12h(next_at)} (if the home stays vacant)."
            else:
                suggested = (
                    f"You’ll be eligible on {_format_date(next_day)} at {_format_time_12h(next_at)} "
                    f"(if the home stays vacant)."
                )
        else:
            # If they’re inside the window but turnover blocks it, reason already explains it.
            suggested = reason or "Not available right now."

    return {
        "eligible": bool(eligible),
        "reason": reason,
        # Backwards compatible (existing UI can keep using this)
        "next_eligible_date": _format_date(next_day) if next_day else None,
        # New: exact timestamp (ISO)
        "next_eligible_at": next_at.isoformat() if next_at else None,
        "suggested_message": suggested,
        "reservation": {
            "arrival_date": reservation.arrival_date.isoformat(),
            "departure_date": reservation.departure_date.isoformat(),
        },
    }
