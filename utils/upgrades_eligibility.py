# utils/upgrades_eligibility.py
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Tuple

from sqlalchemy.orm import Session

from models import Reservation  # your SQLAlchemy model


def is_upgrade_eligible(
    *,
    db: Session,
    upgrade: Any,
    reservation: Reservation,
    today: date | None = None,
) -> Tuple[bool, str]:
    """
    Returns (eligible, reason_if_not).
    Enforces SAME-DAY logic + pre-clear windows for early-check-in and late-checkout.
    """

    if today is None:
        today = date.today()

    if not upgrade or not reservation:
        return False, "Missing stay details for this upgrade."

    slug = (getattr(upgrade, "slug", "") or "").lower().strip()
    arr = reservation.arrival_date
    dep = reservation.departure_date

    # Only these two slugs for now; everything else allowed
    if slug not in {"early-check-in", "late-checkout"}:
        return True, ""

    if slug == "early-check-in":
        # Available starting 2 days before arrival (assuming no turnover on arrival day)
        preclear_day = arr - timedelta(days=2)
        if today < preclear_day:
            return False, "Early check-in becomes available 2 days before arrival (if the home is vacant)."

        turnover_exists = (
            db.query(Reservation.id)
            .filter(
                Reservation.property_id == reservation.property_id,
                Reservation.departure_date == arr,
                Reservation.id != reservation.id,  # exclude the guest's own reservation row
            )
            .first()
            is not None
        )
        if turnover_exists:
            return False, "Early check-in isn’t available because there’s a same-day checkout before your arrival."

        return True, ""

    if slug == "late-checkout":
        # Available starting 1 day before departure (assuming no turnover on departure day)
        preclear_day = dep - timedelta(days=1)
        if today < preclear_day:
            return False, "Late checkout becomes available 1 day before departure (if no one arrives the same day)."

        turnover_exists = (
            db.query(Reservation.id)
            .filter(
                Reservation.property_id == reservation.property_id,
                Reservation.arrival_date == dep,
                Reservation.id != reservation.id,  # exclude the guest's own reservation row
            )
            .first()
            is not None
        )
        if turnover_exists:
            return False, "Late checkout isn’t available because there’s a same-day arrival after your departure."

        return True, ""

    return True, ""
