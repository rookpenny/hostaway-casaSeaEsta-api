from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, time, timedelta, timezone
from typing import Optional, Dict, Any


# -----------------------------
# Data shapes from DB
# -----------------------------

@dataclass
class StayContext:
    property_id: int
    session_id: int

    arrival_date: date
    departure_date: date

    checkin_time: time
    checkout_time: time

    # derived from reservations table
    has_same_day_turnover_on_arrival: bool
    has_same_day_turnover_on_departure: bool

    # optional ops flags (future)
    cleaner_confirmed_ready_early: bool = False
    cleaner_confirmed_ok_late: bool = False


@dataclass
class UpgradeCtx:
    id: int
    property_id: int
    slug: str
    title: str
    price_cents: int
    is_active: bool


@dataclass
class EvalResult:
    eligible: bool
    reason: str = ""
    opens_at: Optional[datetime] = None


# -----------------------------
# Rules config (single source of truth)
# -----------------------------

RULES: Dict[str, Dict[str, Any]] = {
    # Map to slugs by intent detection (see slug_to_kind below)
    "EARLY_CHECKIN": {
        # Your example: open 2 days prior
        "days_prior_window_open": 2,

        # Stop selling too close to check-in (same-day cutoff)
        "cutoff_hours_before_checkin": 6,

        # Require no same-day turnover
        "requires_no_turnover": True,

        # Optional: require housekeeping confirmation
        "requires_cleaner_confirmation": False,
    },
    "LATE_CHECKOUT": {
        "days_prior_window_open": 2,
        "cutoff_hours_before_checkout": 2,
        "requires_no_turnover": True,
        "requires_cleaner_confirmation": False,
    },
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _combine_utc(d: date, t: time) -> datetime:
    return datetime.combine(d, t).replace(tzinfo=timezone.utc)


def slug_to_kind(slug: str) -> Optional[str]:
    """
    Your Upgrade model uses `slug` not `code`.
    We'll map common slugs -> kinds.
    """
    s = (slug or "").lower().strip()

    # early-check-in, early_checkin, earlycheckin, etc.
    if "early" in s and ("check" in s or "arrival" in s):
        return "EARLY_CHECKIN"

    # late-check-out, late_checkout, latecheckout, etc.
    if "late" in s and ("check" in s or "depart" in s):
        return "LATE_CHECKOUT"

    return None


def evaluate_upgrade(
    *,
    upgrade: UpgradeCtx,
    stay: StayContext,
    now: Optional[datetime] = None,
) -> EvalResult:
    now = now or _now_utc()

    if not upgrade.is_active:
        return EvalResult(False, "Not available for this stay.")

    if upgrade.property_id != stay.property_id:
        return EvalResult(False, "Invalid upgrade for this property.")

    kind = slug_to_kind(upgrade.slug)
    if not kind:
        # Unknown upgrade type -> allow if active
        return EvalResult(True)

    cfg = RULES.get(kind, {})

    if kind == "EARLY_CHECKIN":
        return _eval_early(cfg, stay, now)

    if kind == "LATE_CHECKOUT":
        return _eval_late(cfg, stay, now)

    return EvalResult(True)


def _eval_early(cfg: Dict[str, Any], stay: StayContext, now: datetime) -> EvalResult:
    arrival_dt = _combine_utc(stay.arrival_date, stay.checkin_time)

    # window open N days before arrival
    days_prior = int(cfg.get("days_prior_window_open", 0))
    opens = arrival_dt - timedelta(days=days_prior)
    if days_prior > 0 and now < opens:
        return EvalResult(
            False,
            f"Early check-in opens {days_prior} days before arrival.",
            opens_at=opens,
        )

    # cutoff close to check-in
    cutoff_hours = int(cfg.get("cutoff_hours_before_checkin", 0))
    cutoff = arrival_dt - timedelta(hours=cutoff_hours)
    if cutoff_hours > 0 and now > cutoff:
        return EvalResult(False, "It’s too close to check-in to purchase early check-in.")

    # turnover constraint
    if bool(cfg.get("requires_no_turnover", True)) and stay.has_same_day_turnover_on_arrival:
        return EvalResult(False, "Not available due to same-day turnover.")

    # optional ops confirmation
    if bool(cfg.get("requires_cleaner_confirmation", False)) and not stay.cleaner_confirmed_ready_early:
        return EvalResult(False, "Pending housekeeping confirmation.")

    if now >= arrival_dt:
        return EvalResult(False, "Arrival has already started.")

    return EvalResult(True)


def _eval_late(cfg: Dict[str, Any], stay: StayContext, now: datetime) -> EvalResult:
    departure_dt = _combine_utc(stay.departure_date, stay.checkout_time)

    days_prior = int(cfg.get("days_prior_window_open", 0))
    opens = departure_dt - timedelta(days=days_prior)
    if days_prior > 0 and now < opens:
        return EvalResult(
            False,
            f"Late checkout opens {days_prior} days before departure.",
            opens_at=opens,
        )

    cutoff_hours = int(cfg.get("cutoff_hours_before_checkout", 0))
    cutoff = departure_dt - timedelta(hours=cutoff_hours)
    if cutoff_hours > 0 and now > cutoff:
        return EvalResult(False, "It’s too close to checkout to purchase late checkout.")

    if bool(cfg.get("requires_no_turnover", True)) and stay.has_same_day_turnover_on_departure:
        return EvalResult(False, "Not available due to same-day turnover.")

    if bool(cfg.get("requires_cleaner_confirmation", False)) and not stay.cleaner_confirmed_ok_late:
        return EvalResult(False, "Pending housekeeping confirmation.")

    if now >= departure_dt:
        return EvalResult(False, "Checkout has already started.")

    return EvalResult(True)
