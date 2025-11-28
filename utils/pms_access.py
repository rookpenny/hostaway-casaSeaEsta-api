
# utils/pms_access.py

from typing import Optional, Tuple

from models import PMC, Property
from utils.hostaway import get_upcoming_phone_for_listing


def get_pms_access_info(pmc: PMC, prop: Property) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Unified interface for all PMS types.

    Returns:
      phone_last4, door_code, reservation_id

    Right now only Hostaway is implemented.
    Business rule:
      - Door code = last 4 digits of the guest's phone number
    """
    pms = (pmc.pms_integration or "").lower()

    if pms == "hostaway":
        listing_id = prop.pms_property_id
        if not listing_id:
            return None, None, None

        phone_last4, full_phone, reservation_id = get_upcoming_phone_for_listing(listing_id)
        if not phone_last4:
            return None, None, None

        door_code = phone_last4  # 🔑 business rule
        return phone_last4, door_code, reservation_id

    # TODO: Add other PMS integrations here later:
    # if pms == "guesty":
    #     ...
    # if pms == "lodgify":
    #     ...

    # Unsupported PMS (for now)
    return None, None, None
