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
        phone_last4, door_code, reservation_id
    """

    pms = (pmc.pms_integration or "").lower()

    # ---- Hostaway ----
    if pms == "hostaway":
        listing_id = prop.pms_property_id
        if not listing_id:
            return None, None, None

        # 🔑 use the PMC's own Hostaway OAuth credentials
        if not pmc.pms_api_key or not pmc.pms_api_secret:
            return None, None, None

        phone_last4, full_phone, reservation_id = get_upcoming_phone_for_listing(
            str(listing_id),
            pmc.pms_api_key,
            pmc.pms_api_secret,
        )

        if not phone_last4:
            return None, None, None

        door_code = phone_last4  # business rule: door code = last4 of phone
        return phone_last4, door_code, reservation_id

    # ---- Future PMS integrations go here ----
    # if pms == "guesty": ...
    # if pms == "lodgify": ...

    return None, None, None
