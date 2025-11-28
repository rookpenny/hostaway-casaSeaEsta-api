# utils/pms_access.py

from __future__ import annotations
from typing import Tuple, Optional

from models import PMC, Property
from utils.hostaway import get_upcoming_phone_for_listing


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
