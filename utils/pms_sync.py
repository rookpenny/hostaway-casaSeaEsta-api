# utils/pms_sync.py

import os
import requests
from datetime import datetime

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PROPERTIES_TABLE_ID = "tblm0rEfkTDvsr5BU"
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"


def get_access_token(pms: str, client_id: str, client_secret: str) -> str:
    """Returns a bearer token for the given PMS."""
    if pms.lower() == "hostaway":
        url = "https://api.hostaway.com/v1/accessTokens"
        payload = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        response = requests.post(url, data=payload, headers=headers)
        if response.status_code != 200:
            raise Exception(f"Token request failed: {response.text}")
        return response.json()["access_token"]

    # Add more PMS logic here
    raise Exception(f"Unsupported PMS: {pms}")


def fetch_properties(pms: str, token: str) -> list:
    """Fetches properties from the appropriate PMS."""
    if pms.lower() == "hostaway":
        url = "https://api.hostaway.com/v1/listings"
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            raise Exception(f"Hostaway fetch failed: {response.text}")
        return response.json().get("result", [])

    # Add more PMS logic here
    raise Exception(f"Unsupported PMS: {pms}")


def fetch_pmc_lookup():
    """Builds a lookup of PMC credentials from Airtable."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch PMC records: {response.text}")

    records = response.json().get("records", [])
    lookup = {}

    for record in records:
        fields = record.get("fields", {})
        client_id = str(fields.get("PMS Client ID", "")).strip()
        client_secret = str(fields.get("PMS Secret", "")).strip()
        pms = fields.get("PMS Integration", "").strip()
        record_id = record.get("id")

        if client_id and client_secret and pms:
            lookup[client_id] = {
                "client_secret": client_secret,
                "pms": pms,
                "record_id": record_id
            }

    return lookup


def save_to_airtable(properties: list, account_id: str, pms: str):
    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROPERTIES_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    pmc_lookup = fetch_pmc_lookup()
    pmc_record = pmc_lookup.get(account_id)
    pmc_record_id = pmc_record["record_id"] if pmc_record else None
    count = 0

    for prop in properties:
        payload = {
            "fields": {
                "Property Name": prop.get("internalListingName") or prop.get("name"),
                "PMS Property ID": str(prop.get("id")),
                "PMS Account ID": account_id,
                "PMC": [pmc_record_id] if pmc_record_id else [],
                "Notes": prop.get("name"),
                "Active": True,
                "Last Synced": datetime.utcnow().isoformat(),
                "PMS Integration": pms
            }
        }

        res = requests.post(airtable_url, json=payload, headers=headers)
        if res.status_code in (200, 201):
            count += 1
        else:
            print(f"[ERROR] Failed to save property {prop.get('name')}: {res.text}")

    print(f"[INFO] ✅ Saved {count} properties to Airtable")
    return count


def sync_properties(account_id: str):
    """Syncs properties for a single PMC given account_id."""
    pmc_lookup = fetch_pmc_lookup()
    pmc = pmc_lookup.get(account_id)

    if not pmc:
        raise Exception(f"No PMC found for account_id: {account_id}")

    token = get_access_token(pmc["pms"], account_id, pmc["client_secret"])
    properties = fetch_properties(pmc["pms"], token)
    print(f"[INFO] Retrieved {len(properties)} properties from {pmc['pms']}")
    return save_to_airtable(properties, account_id, pmc["pms"])


def sync_all_pmcs():
    """Syncs properties for all PMCs in Airtable."""
    pmc_lookup = fetch_pmc_lookup()
    total = 0

    for account_id in pmc_lookup:
        print(f"[SYNC] 🔄 Syncing properties for PMC Account: {account_id}")
        try:
            count = sync_properties(account_id)
            total += count
        except Exception as e:
            print(f"[ERROR] Failed syncing for {account_id}: {e}")

    print(f"[SYNC] ✅ Total properties synced across all PMCs: {total}")
    return total


# Optional CLI usage
if __name__ == "__main__":
    sync_all_pmcs()
