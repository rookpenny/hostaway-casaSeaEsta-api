# utils/pms_access.py

from __future__ import annotations
from typing import Tuple, Optional

from sqlalchemy.orm import Session

from models import PMC, Property, ChatSession
from utils.hostaway import get_upcoming_phone_for_listing



def get_pms_access_info(
    pmc: PMC,
    prop: Property,
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Resolve guest phone_last4, door_code, reservation_id, guest_name, arrival_date, departure_date
    for a given property.

    Returns:
        (phone_last4, door_code, reservation_id, guest_name, arrival_date, departure_date)
        or (None, None, None, None, None, None) if not found / not applicable.
    """

    phone_last4: Optional[str] = None
    door_code: Optional[str] = None
    reservation_id: Optional[str] = None
    guest_name: Optional[str] = None
    arrival_date: Optional[str] = None
    departure_date: Optional[str] = None

    integration = (prop.pms_integration or pmc.pms_integration or "").lower()

    if not integration:
        print("[PMS] No PMS integration configured for PMC/property")
        return phone_last4, door_code, reservation_id, guest_name, arrival_date, departure_date

    if integration == "hostaway":
        try:
            (
                phone_last4,
                full_phone,
                reservation_id,
                guest_name,
                arrival_date,
                departure_date,
            ) = get_upcoming_phone_for_listing(
                str(prop.pms_property_id),
                pmc.pms_api_key,
                pmc.pms_api_secret,
            )
            # Hostaway does not provide door code; door_code stays None.

        except Exception as e:
            print(f"[Hostaway] Error resolving PMS access info: {e}")
            return None, None, None, None, None, None

    else:
        print(f"[PMS] Integration '{integration}' not yet implemented in get_pms_access_info")

    return phone_last4, door_code, reservation_id, guest_name, arrival_date, departure_date



def ensure_pms_data(db: Session, chat_session: ChatSession) -> None:
    """
    Attach PMS lookup data to a chat session (phone_last4 + reservation_id + guest basics) if missing.

    BEST-EFFORT ONLY — errors should NOT break chat flow.
    """

    # If we already have reservation_id, assume this session is hydrated
    if chat_session.pms_reservation_id:
        return

    prop = db.query(Property).filter(Property.id == chat_session.property_id).first()
    if not prop:
        print(f"[PMS] No property found for chat_session.id={chat_session.id}")
        return

    pmc: Optional[PMC] = getattr(prop, "pmc", None)
    if not pmc and prop.pmc_id:
        pmc = db.query(PMC).filter(PMC.id == prop.pmc_id).first()

    if not pmc:
        print(f"[PMS] No PMC found for property.id={prop.id}")
        return

    try:
        (
            phone_last4,
            door_code,
            reservation_id,
            guest_name,
            arrival_date,
            departure_date,
        ) = get_pms_access_info(pmc, prop)
    except Exception as e:
        print(f"[PMS] Error inside ensure_pms_data: {e}")
        return

    if not reservation_id:
        print(f"[PMS] No upcoming reservation found for property.id={prop.id}")
        return

    chat_session.phone_last4 = phone_last4
    chat_session.pms_reservation_id = reservation_id

    if guest_name:
        chat_session.guest_name = guest_name
    if arrival_date:
        chat_session.arrival_date = arrival_date
    if departure_date:
        chat_session.departure_date = departure_date

    db.add(chat_session)
    db.commit()

