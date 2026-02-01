

def is_upgrade_eligible(
    *,
    db,
    upgrade,
    reservation,
    today: date | None = None,
) -> tuple[bool, str]:
    """
    Returns (eligible, reason_if_not).
    Enforce SAME-DAY logic + pre-clear windows.
    """

    if today is None:
        today = date.today()

    if not upgrade or not reservation:
        return False, "Missing stay details for this upgrade."

    slug = (upgrade.slug or "").lower().strip()
    arr = reservation.arrival_date
    dep = reservation.departure_date

    # Only these two slugs for now; everything else allowed
    if slug not in {"early-check-in", "late-checkout"}:
        return True, ""

    # Find same-day turnover using other reservations.
    # EARLY: turnover is someone departing on arrival day (arr)
    # LATE:  turnover is someone arriving on departure day (dep)
    #
    # You MUST exclude the guest’s own reservation.
    # If you have a "status" column, exclude canceled/no-show.
    from models import Reservation  # or wherever your models live

    if slug == "early-check-in":
        # Eligible window example:
        # - If today is >= arrival-2 days AND there's no departure on arrival day => eligible
        # - If arrival day itself, still eligible only if no turnover (or you decide capacity)
        preclear_day = arr - timedelta(days=2)
        if today < preclear_day:
            return False, "Early check-in becomes available 2 days before arrival (if the home is vacant)."

        turnover_exists = (
            db.query(Reservation.id)
            .filter(
                Reservation.property_id == reservation.property_id,
                Reservation.departure_date == arr,
                Reservation.id != reservation.id,
            )
            .first()
            is not None
        )

        if turnover_exists:
            return False, "Early check-in isn’t available because there’s a same-day checkout before your arrival."

        return True, ""

    if slug == "late-checkout":
        # Eligible window example:
        # - If today is >= departure-1 day AND there's no arrival on departure day => eligible
        preclear_day = dep - timedelta(days=1)
        if today < preclear_day:
            return False, "Late checkout becomes available 1 day before departure (if no one arrives the same day)."

        turnover_exists = (
            db.query(Reservation.id)
            .filter(
                Reservation.property_id == reservation.property_id,
                Reservation.arrival_date == dep,
                Reservation.id != reservation.id,
            )
            .first()
            is not None
        )

        if turnover_exists:
            return False, "Late checkout isn’t available because there’s a same-day arrival after your departure."

        return True, ""

    return True, ""
