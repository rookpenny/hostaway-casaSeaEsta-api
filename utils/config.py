import os
import requests
from functools import lru_cache

@lru_cache(maxsize=128)
def load_property_config(slug: str) -> dict:
    base_id = os.getenv("AIRTABLE_CONFIG_BASE_ID")
    table_id = os.getenv("AIRTABLE_CONFIG_TABLE_ID")  # e.g. "tbl123ABC456xyz"
    api_key = os.getenv("AIRTABLE_API_KEY")

    url = f"https://api.airtable.com/v0/{base_id}/{table_id}"
    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    params = {
        "filterByFormula": f"LOWER(property_slug) = '{slug.lower()}'"
    }

    response = requests.get(url, headers=headers, params=params)
    records = response.json().get("records", [])

    if not records:
        raise ValueError(f"No config found for slug: {slug}")

    fields = records[0]["fields"]
    return {
        "listing_id": str(fields["listing_id"]),
        "property_name": fields["property_name"],
        "emergency_phone": fields.get("emergency_phone", ""),
        "default_checkin_time": int(fields.get("default_checkin_time", 16)),
        "default_checkout_time": int(fields.get("default_checkout_time", 10))
    }
