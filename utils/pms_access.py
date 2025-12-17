# utils/pms_access.py
from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from models import PMC, PMCIntegration, Property, ChatSession
from utils.hostaway import get_upcoming_phone_for_listing


AccessTuple = Tuple[
    Optional[str],  # phone_last4
    Optional[str],  # door_code
    Optional[str],  # reservation_id
    Optional[str],  # guest_name
    Optional[str],  # arrival_date (YYYY-MM-DD)
    Optional[str],  # departure_date (YYYY-MM-DD)
]


def _provider_for_property(pmc: PMC, prop: Property) -> str:
    """
    New source of truth: Property.provider (preferred), otherwise PMC.pms_integration (legacy fallback).
    """
    prop_provider = (getattr(prop, "provider", None) or "").strip().lower()
    if prop_provider:
        return prop_provider

    # legacy fallback (try not to rely on this long-term)
    pmc_provider = (getattr(pmc, "pms_integration", None) or "").strip().lower()
    return pmc_provider


def _integration_for_property(db: Session, prop: Property) -> Optional[PMCIntegration]:
    """
    New source of truth: Property.integration_id -> PMCIntegration row.
    """
    integration_id = getattr(prop, "integration_id", None)
    if not integration_id:
        return None

    return (
        db.query(PMCIntegration)
        .filter(
            PMCIntegration.id == int(integration_id),
            PMCIntegration.pmc_id == int(prop.pmc_id),
        )
        .first()
    )


def get_pms_access_info(db: Session, pmc: PMC, prop: Property) -> AccessTuple:
    """
    Resolve guest phone_last4, door_code, reservation_id, guest_name, arrival_date, departure_date
    for a given property.

    Returns:
        (phone_last4, door_code, reservation_id, guest_name, arrival_date, departure_date)
        or (None, None, None, None, None, None) if not found / not applicable.
    """
    phone_last4 = door_code = reservation_id = guest_name = arrival_date = departure_date = None

    provider = _provider_for_property(pmc, prop)
    integration = (getattr(prop, "provider", None) or getattr(pmc, "pms_integration", None) or "").lower()

   
    if not provider:
        print("[PMS] No provider found for PMC/property")
        return phone_last4, door_code, reservation_id, guest_name, arrival_date, departure_date

    # ✅ Hostaway (integration-based)
    if provider == "hostaway":
        if not getattr(prop, "pms_property_id", None):
            print("[Hostaway] Property missing pms_property_id")
            return phone_last4, door_code, reservation_id, guest_name, arrival_date, departure_date

        integ = _integration_for_property(db, prop)
        if not integ:
            print("[Hostaway] Property missing integration or integration not found")
            return phone_last4, door_code, reservation_id, guest_name, arrival_date, departure_date

        account_id = (integ.account_id or "").strip()
        api_secret = (integ.api_secret or "").strip()
        if not account_id or not api_secret:
            print("[Hostaway] Integration missing account_id/api_secret")
            return phone_last4, door_code, reservation_id, guest_name, arrival_date, departure_date

        try:
            (
                phone_last4,
                _full_phone,  # noqa: F841 (kept for compatibility)
                reservation_id,
                guest_name,
                arrival_date,
                departure_date,
            ) = get_upcoming_phone_for_listing(
                listing_id=str(prop.pms_property_id),
                client_id=account_id,
                client_secret=api_secret,
            )
            # Hostaway does not provide a door code here; you use last4 as code in your app logic.
        except Exception as e:
            print(f"[Hostaway] Error resolving PMS access info: {e}")
            return None, None, None, None, None, None

        return phone_last4, door_code, reservation_id, guest_name, arrival_date, departure_date

    print(f"[PMS] Provider '{provider}' not yet implemented in get_pms_access_info")
    return phone_last4, door_code, reservation_id, guest_name, arrival_date, departure_date


def _to_date(value):
    """Normalize date/datetime/ISO-string to date."""
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value[:10]).date()
        except Exception:
            return None
    return None


def compute_reservation_status(arrival_date, departure_date) -> str:
    """
    Returns: 'pre_booking', 'active', or 'post_stay'
    arrival_date/departure_date may be strings or dates.
    """
    a = _to_date(arrival_date)
    d = _to_date(departure_date)

    if not a or not d:
        return "pre_booking"

    today = date.today()

    if a <= today <= d:
        return "active"
    if today > d:
        return "post_stay"
    return "pre_booking"


def ensure_pms_data(db: Session, chat_session: ChatSession) -> None:
    """
    Attach PMS lookup data to a chat session (phone_last4 + reservation info).

    BEST-EFFORT ONLY — errors should NOT break chat flow.
    """

    prop = db.query(Property).filter(Property.id == int(chat_session.property_id)).first()
    if not prop:
        print(f"[PMS] No property found for chat_session.id={chat_session.id}")
        return

    pmc: Optional[PMC] = getattr(prop, "pmc", None)
    if not pmc and prop.pmc_id:
        pmc = db.query(PMC).filter(PMC.id == int(prop.pmc_id)).first()

    if not pmc:
        print(f"[PMS] No PMC found for property.id={prop.id}")
        return

    # Only call PMS if we don't already have a reservation id on the session
    if not getattr(chat_session, "pms_reservation_id", None):
        try:
            (
                phone_last4,
                door_code,  # noqa: F841
                reservation_id,
                guest_name,
                arrival_date,
                departure_date,
            ) = get_pms_access_info(db, pmc, prop)
        except Exception as e:
            print(f"[PMS] Error inside ensure_pms_data: {e}")
            return

        if not reservation_id:
            chat_session.reservation_status = "pre_booking"
            db.add(chat_session)
            db.commit()
            return

        chat_session.phone_last4 = phone_last4
        chat_session.pms_reservation_id = reservation_id

        if guest_name:
            chat_session.guest_name = guest_name
        if arrival_date:
            chat_session.arrival_date = arrival_date
        if departure_date:
            chat_session.departure_date = departure_date

    # Always compute status (handles rollover without re-hitting PMS)
    chat_session.reservation_status = compute_reservation_status(
        chat_session.arrival_date,
        chat_session.departure_date,
    )

    db.add(chat_session)
    db.commit()
