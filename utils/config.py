import os
import requests
import json
from functools import lru_cache

@lru_cache(maxsize=128)
def load_property_config(slug: str) -> dict:
    """
    Load property config from Airtable, with fallback to local file (if available).
    """
    # First try: Airtable
    try:
        base_id = os.getenv("AIRTABLE_CONFIG_BASE_ID")
        table_id = os.getenv("AIRTABLE_CONFIG_TABLE_ID")
        api_key = os.getenv("AIRTABLE_API_KEY")

        url = f"https://api.airtable.com/v0/{base_id}/{table_id}"
        headers = {
            "Authorization": f"Bearer {api_key}"
        }

        params = {
            "filterByFormula": f"LOWER(property_slug) = '{slug.lower()}'"
        }

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()

        records = response.json().get("records", [])

        if records:
            fields = records[0]["fields"]
            return {
                "listing_id": str(fields["listing_id"]),
                "property_name": fields["property_name"],
                "emergency_phone": fields.get("emergency_phone", ""),
                "default_checkin_time": int(fields.get("default_checkin_time", 16)),
                "default_checkout_time": int(fields.get("default_checkout_time", 10))
            }

    except Exception as e:
        print(f"[Config] Airtable fetch failed for {slug}: {e}")

    # Fallback: Local JSON file
    try:
        path = f"data/{slug}/config.json"
        if not os.path.exists(path):
            raise FileNotFoundError(f"No local config at {path}")

        with open(path, "r") as f:
            config = json.load(f)

        return {
            "listing_id": str(config.get("listing_id")),
            "property_name": config.get("property_name"),
            "emergency_phone": config.get("emergency_phone", ""),
            "default_checkin_time": int(config.get("default_checkin_time", 16)),
            "default_checkout_time": int(config.get("default_checkout_time", 10))
        }

    except Exception as e:
        raise ValueError(f"[Config] Failed to load config for {slug}: {e}")
