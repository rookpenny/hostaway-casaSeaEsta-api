# utils/pms_access.py

from __future__ import annotations
from typing import Tuple, Optional

from models import PMC, Property, ChatSession
from utils.hostaway import get_upcoming_phone_for_listing
from sqlalchemy.orm import Session

def get_pms_access_info(
    pmc: PMC,
    prop: Property,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Resolve guest phone_last4, door_code, and pms_reservation_id for a given property.

    Returns:
        (phone_last4, door_code, pms_reservation_id)
        or (None, None, None) if we can't resolve anything.
    """

    integration = (prop.pms_integration or pmc.pms_integration or "").lower()

    # No PMS integration configured
    if not integration:
        print("[PMS] No PMS integration configured for PMC/property")
        return None, None, None


    from sqlalchemy.orm import Session
from models import Property, ChatSession


def ensure_pms_data(db: Session, chat_session: ChatSession) -> None:
    from utils.pms_access import get_pms_access_info  # or just use it directly if same file

    prop = db.query(Property).filter(Property.id == chat_session.property_id).first()
    if not prop:
        print(f"[PMS] No property found for chat_session.id={chat_session.id}")
        return

    pmc = prop.pmc
    if not pmc:
        print(f"[PMS] No PMC found for property.id={prop.id}")
        return

    phone_last4, door_code, reservation_id = get_pms_access_info(pmc, prop)

    if not reservation_id:
        return

    chat_session.phone_last4 = phone_last4
    chat_session.pms_reservation_id = reservation_id
    db.add(chat_session)
    db.commit()

    
    
    # --- Hostaway integration -----------------------------------------------
    if integration == "hostaway":
        listing_id = prop.pms_property_id
        if not listing_id:
            print("[PMS] Hostaway integration but property.pms_property_id is missing")
            return None, None, None

        if not pmc.pms_api_key or not pmc.pms_api_secret:
            print("[PMS] Hostaway integration but PMC is missing API credentials")
            return None, None, None

        phone_last4, full_phone, reservation_id = get_upcoming_phone_for_listing(
            str(listing_id),
            pmc.pms_api_key,     # client_id
            pmc.pms_api_secret,  # client_secret
        )

        if not reservation_id:
            print("[PMS] No upcoming reservation found for this Hostaway listing")
            return None, None, None

        # Door code logic: "last 4 of phone" as per your requirement
        door_code = phone_last4
        return phone_last4, door_code, reservation_id

    # --- Other PMS types (future) -------------------------------------------
    # e.g. "guesty", "lodgify", etc.
    print(f"[PMS] Unsupported PMS integration type: {integration}")
    return None, None, None
