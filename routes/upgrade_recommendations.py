# routes/upgrade_recommendations.py
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import Property, Upgrade, Reservation

from utils.upgrades_eligibility import is_upgrade_eligible

router = APIRouter()


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


def _get_upcoming_or_current_reservation(db: Session, property_id: int, phone_last4: Optional[str]) -> Optional[Reservation]:
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
    # Keep simple for now (your UI can prettify)
    return d.isoformat()


def _next_eligible_date(upgrade_slug: str, reservation: Reservation, today: date) -> Optional[date]:
    """
    Only for early-check-in / late-checkout. Returns the earliest date the upgrade
    could become eligible based on your preclear windows (ignoring turnover).
    Turnover is re-checked at request-time anyway.
    """
    arr = reservation.arrival_date
    dep = reservation.departure_date

    if upgrade_slug == "early-check-in":
        return arr - timedelta(days=2)
    if upgrade_slug == "late-checkout":
        return dep - timedelta(days=1)
    return None


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

    # Find reservation for this guest
    phone_last4 = None
    try:
        # your ChatSession stores phone_last4; simplest is stash it in cookie session at verify
        # If you haven’t yet: set request.session[f"guest_phone_last4_{property_id}"] = phone_last4 during verify.
        phone_last4 = request.session.get(f"guest_phone_last4_{property_id}")
    except Exception:
        phone_last4 = None

    reservation = _get_upcoming_or_current_reservation(db, property_id=int(property_id), phone_last4=phone_last4)
    if not reservation:
        # If you don’t have reservations synced, you can still say “ask host”
        return {
            "eligible": False,
            "reason": "We can’t verify your stay dates yet, so we can’t confirm upgrade availability.",
            "next_eligible_date": None,
            "suggested_message": "Try again later, or message your host for availability.",
        }

    today = date.today()
    slug = (getattr(upgrade, "slug", "") or "").lower().strip()

    eligible, reason = is_upgrade_eligible(db=db, upgrade=upgrade, reservation=reservation, today=today)

    # Compute “next eligible” if not eligible (only meaningful for the 2 core upgrades)
    next_day = _next_eligible_date(slug, reservation, today=today)

    # Friendly recommendation copy (this is the “AI-assisted” part; later you can swap to OpenAI)
    suggested = ""
    if eligible:
        suggested = "✅ You’re eligible now."
    else:
        if next_day and today < next_day:
            # “eligible tomorrow” style
            if next_day == today + timedelta(days=1):
                suggested = "You’ll be eligible tomorrow (if the home stays vacant)."
            else:
                suggested = f"You’ll be eligible on {_format_date(next_day)} (if the home stays vacant)."
        else:
            # If they’re inside the window but turnover blocks it, reason already says why
            suggested = reason or "Not available right now."

    return {
        "eligible": eligible,
        "reason": reason,
        "next_eligible_date": _format_date(next_day) if next_day else None,
        "suggested_message": suggested,
        "reservation": {
            "arrival_date": reservation.arrival_date.isoformat(),
            "departure_date": reservation.departure_date.isoformat(),
        },
    }
