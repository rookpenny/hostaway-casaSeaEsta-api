# utils/pms_access.py

from typing import Optional, Tuple
from models import PMC, Property
from utils.hostaway import get_upcoming_phone_for_listing  # current adapter
# from utils.guesty import ... (future)
# from utils.lodgify import ... (future)

def get_pms_access_info(pmc: PMC, prop: Property) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Unified interface for all PMS types.

    Returns:
      phone_last4, door_code, reservation_id
    """
    pms = (pmc.pms_integration or "").lower()

    if pms == "hostaway":
        listing_id = prop.pms_property_id
        if not listing_id:
            return None, None, None

        phone_last4, full_phone, reservation_id = get_upcoming_phone_for_listing(listing_id)
        if not phone_last4:
            return None, None, None

        # ✅ Business rule: door code = phone_last4
        door_code = phone_last4
        return phone_last4, door_code, reservation_id

    # elif pms == "guesty":
    #     return guesty_get_access_info(...)

    # elif pms == "lodgify":
    #     return lodgify_get_access_info(...)

    # Default / unsupported PMS
    return None, None, None
