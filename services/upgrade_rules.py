from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, time, timedelta, timezone
from typing import Optional, Tuple, Dict, Any


# -----------------------------
# Data shapes your repo should provide
# -----------------------------

@dataclass
class StayContext:
    property_id: int
    session_id: str

    arrival_date: date
    departure_date: date

    checkin_time: time          # default check-in (e.g. 16:00)
    checkout_time: time         # default checkout (e.g. 10:00)

    # Operational flags you can compute from reservations/turnovers:
    has_same_day_turnover_on_arrival: bool
    has_same_day_turnover_on_departure: bool

    # Optional operational constraints:
    cleaner_confirmed_ready_early: bool = False
    cleaner_confirmed_ok_late: bool = False
    is_vip: bool = False


@dataclass
class Upgrade:
    id: int
    property_id: int
    code: str  # e.g. "EARLY_CHECKIN", "LATE_CHECKOUT"
    title: str
    price_cents: int
    enabled: bool = True


@dataclass
class EvalResult:
    eligible: bool
    reason: str = ""
    opens_at: Optional[datetime] = None
    # You can expand with price overrides, etc.


# -----------------------------
# Rules config (enterprise-friendly)
# -----------------------------
DEFAULT_RULES = {
    # Early check-in: offer same-day only if no turnover + ops constraints satisfied
    "EARLY_CHECKIN": {
        "same_day_cutoff_hours_before_checkin": 6,   # example: stop selling too close to check-in
        "days_prior_window_open": 2,                 # your example: 2 days prior, if no arrival-day turnover expected
        "requires_no_turnover": True,
        "requires_cleaner_confirmation": False,      # flip true if you want strict ops gating
    },
    "LATE_CHECKOUT": {
        "same_day_cutoff_hours_before_checkout": 2,  # example: stop selling too close to checkout
        "days_prior_window_open": 2,
        "requires_no_turnover": True,
        "requires_cleaner_confirmation": False,
    },
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _combine_local(d: date, t: time) -> datetime:
    # If you have property timezones, convert properly.
    # For now treat as UTC-naive-ish by attaching UTC.
    return datetime.combine(d, t).replace(tzinfo=timezone.utc)


def evaluate_upgrade(
    *,
    upgrade: Upgrade,
    stay: StayContext,
    rules: Dict[str, Any] = DEFAULT_RULES,
    now: Optional[datetime] = None,
) -> EvalResult:
    """
    Backend source of truth:
      - returns eligible + reason + opens_at
      - keep it deterministic and unit-testable
    """
    now = now or _now_utc()

    if not upgrade.enabled:
        return EvalResult(False, "Not available for this stay.")

    if upgrade.property_id != stay.property_id:
        return EvalResult(False, "Invalid upgrade for this property.")

    code = (upgrade.code or "").upper().strip()
    cfg = rules.get(code)

    if not cfg:
        # Unknown upgrade type -> default allow only if enabled
        return EvalResult(True)

    if code == "EARLY_CHECKIN":
        return _eval_early_checkin(cfg, stay, now)

    if code == "LATE_CHECKOUT":
        return _eval_late_checkout(cfg, stay, now)

    # Default allow
    return EvalResult(True)


def _eval_early_checkin(cfg: Dict[str, Any], stay: StayContext, now: datetime) -> EvalResult:
    arrival_dt = _combine_local(stay.arrival_date, stay.checkin_time)

    # Too early in lifecycle? (only sell starting N days prior)
    days_prior = int(cfg.get("days_prior_window_open", 0))
    window_opens = arrival_dt - timedelta(days=days_prior)
    if days_prior > 0 and now < window_opens:
        return EvalResult(
            False,
            f"Early check-in opens {days_prior} days before arrival.",
            opens_at=window_opens,
        )

    # Cutoff close to check-in (same-day cutoff)
    cutoff_hours = int(cfg.get("same_day_cutoff_hours_before_checkin", 0))
    cutoff = arrival_dt - timedelta(hours=cutoff_hours)
    if cutoff_hours > 0 and now > cutoff:
        return EvalResult(False, "It’s too close to check-in to purchase early check-in.")

    # Turnover constraints
    if bool(cfg.get("requires_no_turnover", True)) and stay.has_same_day_turnover_on_arrival:
        return EvalResult(False, "Not available due to same-day turnover.")

    # Optional ops confirmation
    if bool(cfg.get("requires_cleaner_confirmation", False)) and not stay.cleaner_confirmed_ready_early:
        return EvalResult(False, "Pending housekeeping confirmation.")

    # Past arrival -> no
    if now >= arrival_dt:
        return EvalResult(False, "Arrival has already started.")

    return EvalResult(True)


def _eval_late_checkout(cfg: Dict[str, Any], stay: StayContext, now: datetime) -> EvalResult:
    departure_dt = _combine_local(stay.departure_date, stay.checkout_time)

    days_prior = int(cfg.get("days_prior_window_open", 0))
    window_opens = departure_dt - timedelta(days=days_prior)
    if days_prior > 0 and now < window_opens:
        return EvalResult(
            False,
            f"Late checkout opens {days_prior} days before departure.",
            opens_at=window_opens,
        )

    cutoff_hours = int(cfg.get("same_day_cutoff_hours_before_checkout", 0))
    cutoff = departure_dt - timedelta(hours=cutoff_hours)
    if cutoff_hours > 0 and now > cutoff:
        return EvalResult(False, "It’s too close to checkout to purchase late checkout.")

    if bool(cfg.get("requires_no_turnover", True)) and stay.has_same_day_turnover_on_departure:
        return EvalResult(False, "Not available due to same-day turnover.")

    if bool(cfg.get("requires_cleaner_confirmation", False)) and not stay.cleaner_confirmed_ok_late:
        return EvalResult(False, "Pending housekeeping confirmation.")

    # Past departure -> no
    if now >= departure_dt:
        return EvalResult(False, "Checkout has already started.")

    return EvalResult(True)
