# utils/pms_access.py

from typing import Optional, Tuple

from models import PMC, Property
from utils.hostaway import get_upcoming_phone_for_listing


def get_pms_access_info(
    pmc: PMC,
    prop: Property
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Unified PMS access lookup.

    Returns:
        (phone_last4, door_code, reservation_id)
        or (None, None, None) if not available.
    """

    pms = (pmc.pms_integration or "").lower()

    # ---- Hostaway ----
    if pms == "hostaway":
        listing_id = prop.pms_property_id
        if not listing_id:
            print("[PMS] Hostaway selected but property has no pms_property_id")
            return None, None, None

        # ✅ Use PMC-specific Hostaway credentials (Client ID / Secret)
        if not pmc.pms_api_key or not pmc.pms_api_secret:
            print("[PMS] Hostaway selected but PMC missing pms_api_key or pms_api_secret")
            return None, None, None

        phone_last4, full_phone, reservation_id = get_upcoming_phone_for_listing(
            str(listing_id),
            pmc.pms_api_key,      # Hostaway Client ID stored on PMC
            pmc.pms_api_secret,   # Hostaway Client Secret stored on PMC
        )

        if not phone_last4:
            print("[PMS] No upcoming reservation found for this Hostaway listing")
            return None, None, None

        # Business rule: door code = phone last 4
        door_code = phone_last4
        return phone_last4, door_code, reservation_id

    # ---- Other PMS integrations go here later ----
    # if pms == "guesty": ...
    # if pms == "lodgify": ...

    return None, None, None
