# ----------- GLOBAL DEFAULTS -----------
DEFAULT_EMERGENCY_PHONE = "+1-650-313-3724"

# These are used only as fallbacks or before config migration is complete.
FALLBACK_PROPERTY_CONFIGS = {
    "casa-sea-esta": {
        "listing_id": "256853",
        "emergency_phone": DEFAULT_EMERGENCY_PHONE,
    },
}

# This controls which listings are allowed to be used via the API.
ALLOWED_LISTING_IDS = {cfg["listing_id"] for cfg in FALLBACK_PROPERTY_CONFIGS.values()}
