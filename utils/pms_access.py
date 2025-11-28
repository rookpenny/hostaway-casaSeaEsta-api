# utils/pms_access.py

from __future__ import annotations
from typing import Tuple, Optional

from sqlalchemy.orm import Session

from models import PMC, Property, ChatSession
from utils.hostaway import get_upcoming_phone_for_listing


def get_pms_access_info(
    pmc: PMC,
    prop: Property,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Resolve guest phone_last4, door_code, and pms_reservation_id for a given property.

    Returns:
        (phone_last4, door_code, reservation_id)
        or (None, None, None) if not found / not applicable.
    """

    phone_last4: Optional[str] = None
    door_code: Optional[str] = None
    reservation_id: Optional[str] = None

    # Determine PMS integration on property or fall back to PMC-level setting
    integration = (prop.pms_integration or pmc.pms_integration or "").lower()

    if not integration:
        print("[PMS] No PMS integration configured for PMC/property")
        return phone_last4, door_code, reservation_id

    # --- PMS: HOSTAWAY ---
    if integration == "hostaway":
        try:
            # The function get_upcoming_phone_for_listing should return:
            # phone_last4, full_phone, reservation_id
            phone_last4, full_phone, reservation_id = get_upcoming_phone_for_listing(
                str(prop.pms_property_id),
                pmc.pms_api_key,
                pmc.pms_api_secret,
            )

            # Hostaway does not provide door code, so let door_code remain None.
            # If you later store a door code in the PMS or config, fill it in here.

        except Exception as e:
            print(f"[Hostaway] Error resolving PMS access info: {e}")
            return None, None, None

    else:
        print(f"[PMS] Integration '{integration}' not yet implemented in get_pms_access_info")

    return phone_last4, door_code, reservation_id


def ensure_pms_data(db: Session, chat_session: ChatSession) -> None:
    """
    Attach PMS lookup data to a chat session (phone_last4 + reservation_id) if missing.

    BEST-EFFORT ONLY — errors should NOT break chat flow.
    """

    # Skip if already populated
    if chat_session.phone_last4 and chat_session.pms_reservation_id:
        return

    # Lookup the property
    prop = db.query(Property).filter(Property.id == chat_session.property_id).first()
    if not prop:
        print(f"[PMS] No property found for chat_session.id={chat_session.id}")
        return

    # Get PMC via relationship or fallback query
    pmc: Optional[PMC] = getattr(prop, "pmc", None)
    if not pmc and prop.pmc_id:
        pmc = db.query(PMC).filter(PMC.id == prop.pmc_id).first()

    if not pmc:
        print(f"[PMS] No PMC found for property.id={prop.id}")
        return

    try:
        phone_last4, door_code, reservation_id = get_pms_access_info(pmc, prop)
    except Exception as e:
        print(f"[PMS] Error inside ensure_pms_data: {e}")
        return

    if not reservation_id:
        print(f"[PMS] No upcoming reservation found for property.id={prop.id}")
        return

    # Update session
    chat_session.phone_last4 = phone_last4
    chat_session.pms_reservation_id = reservation_id

    db.add(chat_session)
    db.commit()
