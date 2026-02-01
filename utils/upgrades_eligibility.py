# utils/upgrades_eligibility.py
from __future__ import annotations

from datetime import date, timedelta
from typing import Tuple, Any

from sqlalchemy.orm import Session

from models import Reservation


def is_upgrade_eligible(
    *,
    db: Session,
    upgrade: Any,
    reservation: Reservation,
    today: date | None = None,
) -> Tuple[bool, str]:
    """
    Returns (eligible, reason_if_not).

    Rules enforced:
    - EARLY CHECK-IN
        • Available starting 2 days before arrival
        • NOT available if another guest checks out on arrival date
    - LATE CHECKOUT
        • Available starting 1 day before departure
        • NOT available if another guest checks in on departure date
    """

    if today is None:
        today = date.today()

    if not upgrade or not reservation:
        return False, "Missing stay details for this upgrade."

    arr = reservation.arrival_date
    dep = reservation.departure_date

    if not arr or not dep:
        return False, "Missing stay dates for this reservation."

    slug = (getattr(upgrade, "slug", "") or "").lower().strip()

    # Only gate these upgrades — everything else is always allowed
    if slug not in {"early-check-in", "late-checkout"}:
        return True, ""

    # --------------------------------------------------
    # EARLY CHECK-IN
    # --------------------------------------------------
    if slug == "early-check-in":
        # Unlock window: 2 days before arrival
        if today < (arr - timedelta(days=2)):
            return (
                False,
                "Early check-in becomes available 2 days before arrival (if the home is vacant).",
            )

        # Same-day turnover check:
        # Someone else checks OUT on my arrival date
        turnover_exists = (
            db.query(Reservation.id)
            .filter(
                Reservation.property_id == reservation.property_id,
                Reservation.departure_date == arr,
                Reservation.id != reservation.id,  # exclude this guest
            )
            .first()
            is not None
        )

        if turnover_exists:
            return (
                False,
                "Early check-in isn’t available due to a same-day checkout before your arrival.",
            )

        return True, ""

    # --------------------------------------------------
    # LATE CHECKOUT
    # --------------------------------------------------
    if slug == "late-checkout":
        # Unlock window: 1 day before departure
        if today < (dep - timedelta(days=1)):
            return (
                False,
                "Late checkout becomes available 1 day before departure (if no one arrives the same day).",
            )

        # Same-day turnover check:
        # Someone else checks IN on my departure date
        turnover_exists = (
            db.query(Reservation.id)
            .filter(
                Reservation.property_id == reservation.property_id,
                Reservation.arrival_date == dep,
                Reservation.id != reservation.id,  # exclude this guest
            )
            .first()
            is not None
        )

        if turnover_exists:
            return (
                False,
                "Late checkout isn’t available due to a same-day arrival after your departure.",
            )

        return True, ""

    return True, ""
