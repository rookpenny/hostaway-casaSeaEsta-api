import os
import requests
from datetime import datetime

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PROPERTIES_TABLE_ID = "tblm0rEfkTDvsr5BU"  # Properties table ID
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"         # PMC table ID


def fetch_pmc_lookup():
    """Fetch PMC configs from Airtable and return a dict of client_id -> credentials."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch PMC records: {response.text}")

    records = response.json().get("records", [])
    lookup = {}

    def default_base_url(pms):
        pms = pms.lower()
        return {
            "hostaway": "https://api.hostaway.com/v1",
            "guesty": "https://open-api.guesty.com/v1",
            "lodgify": "https://api.lodgify.com/v1"
        }.get(pms, "https://api.example.com/v1")  # fallback for future

    for record in records:
        fields = record.get("fields", {})
        account_id = str(fields.get("PMS Account ID", "")).strip()
        client_id = str(fields.get("PMS Client ID", "")).strip()  # ✅ ADDED
        client_secret = str(fields.get("PMS Secret", "")).strip()
        pms = fields.get("PMS Integration", "").strip().lower()
        sync_enabled = fields.get("Sync Enabled", True)

        base_url = fields.get("API Base URL", "").strip() or default_base_url(pms)
        version = fields.get("API Version", "").strip()

        if account_id and client_id and client_secret and sync_enabled:
            lookup[account_id] = {
                "record_id": record["id"],
                "client_id": client_id,  # ✅ ADDED
                "client_secret": client_secret,
                "pms": pms,
                "base_url": base_url,
                "version": version,
            }

    return lookup


def get_access_token(client_id: str, client_secret: str, base_url: str, pms: str) -> str:
    if pms == "hostaway":
        token_url = f"{base_url}/accessTokens"
        payload = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    elif pms == "guesty":
        token_url = f"{base_url}/auth"
        payload = {
            "clientId": client_id,
            "clientSecret": client_secret
        }
        headers = {"Content-Type": "application/json"}
    else:
        raise Exception(f"Unsupported PMS for auth: {pms}")

    response = requests.post(token_url, json=payload if headers["Content-Type"] == "application/json" else payload, headers=headers)

    if response.status_code != 200:
        raise Exception(f"Token request failed: {response.text}")

    return response.json()["access_token"]


def fetch_properties(access_token: str, base_url: str, pms: str):
    """Fetch property list from PMS API using bearer token."""
    url = f"{base_url}/listings" if pms == "hostaway" else f"{base_url}/properties"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        raise Exception(f"Failed to fetch properties: {response.text}")

    if pms == "hostaway":
        return response.json().get("result", [])
    else:
        return response.json().get("properties", [])


def save_to_airtable(properties, account_id, pmc_record_id):
    """Write fetched property records to Airtable."""
    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROPERTIES_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    count = 0
    for prop in properties:
        prop_id = str(prop.get("id"))
        name = prop.get("internalListingName") or prop.get("name")

        payload = {
            "fields": {
                "Property Name": name,
                "Property External ID": prop_id,
                "Account ID": account_id,
                "PMC": [pmc_record_id],
                "Active": True,
                "Last Synced": datetime.utcnow().isoformat(),
                "Notes": prop.get("name")  # Optional internal label
            }
        }

        res = requests.post(airtable_url, json=payload, headers=headers)
        if res.status_code in (200, 201):
            count += 1
        else:
            print(f"[ERROR] Failed to save property {name}: {res.text}")

    return count


def sync_properties(account_id: str):
    """Sync a single PMC by account ID."""
    pmcs = fetch_pmc_lookup()
    print(f"[DEBUG] Fetched PMCs: {list(pmcs.keys())}")
    if account_id not in pmcs:
        raise Exception(f"PMC not found for account ID: {account_id}")

    pmc = pmcs[account_id]
    token = get_access_token(account_id, pmc["client_secret"], pmc["base_url"], pmc["pms"])
    properties = fetch_properties(token, pmc["base_url"], pmc["pms"])
    count = save_to_airtable(properties, account_id, pmc["record_id"])

    print(f"[SYNC] ✅ Saved {count} properties for {account_id}")
    return count


def sync_all_pmcs():
    """Loop through all PMCs in Airtable and sync their properties."""
    total = 0
    pmcs = fetch_pmc_lookup()
    for account_id in pmcs.keys():
        print(f"[SYNC] 🔄 Syncing PMC {account_id}")
        try:
            total += sync_properties(account_id)
        except Exception as e:
            print(f"[ERROR] Failed syncing {account_id}: {e}")
    print(f"[SYNC] ✅ Total properties synced: {total}")
    return total


# For local test
if __name__ == "__main__":
    sync_all_pmcs()
