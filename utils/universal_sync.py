import os
import requests
from datetime import datetime

# Airtable config
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PROPERTIES_TABLE_ID = "tblm0rEfkTDvsr5BU"
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"

def fetch_pmc_lookup():
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch PMC records: {response.text}")

    lookup = {}
    for record in response.json().get("records", []):
        fields = record.get("fields", {})
        client_id = str(fields.get("PMS Client ID", "")).strip()
        client_secret = str(fields.get("PMS Secret", "")).strip()
        base_url = str(fields.get("API Base URL", "")).strip()
        enabled = fields.get("Sync Enabled", True)
        pms = fields.get("PMS Integration", "").strip()

        if client_id and client_secret and base_url and enabled:
            lookup[client_id] = {
                "record_id": record["id"],
                "client_secret": client_secret,
                "api_base_url": base_url,
                "pms": pms
            }

    return lookup

def get_access_token(base_url: str, client_id: str, client_secret: str) -> str:
    url = f"{base_url}/v1/accessTokens"
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

def fetch_properties(base_url: str, token: str) -> list:
    url = f"{base_url}/v1/listings"
    headers = {"Authorization": f"Bearer {token}"}

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch properties: {response.text}")

    return response.json().get("result", [])

def save_to_airtable(properties: list, account_id: str, pmc_record_id: str):
    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROPERTIES_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    count = 0
    for prop in properties:
        payload = {
            "fields": {
                "Property Name": prop.get("internalListingName") or prop.get("name"),
                "Property External ID": str(prop.get("id")),
                "PMS Account ID": account_id,
                "PMC": [pmc_record_id],
                "Notes": prop.get("name", ""),
                "Active": True,
                "Last Synced": datetime.utcnow().isoformat()
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
    pmcs = fetch_pmc_lookup()
    pmc = pmcs.get(account_id)

    if not pmc:
        raise Exception(f"No PMC found for Account ID: {account_id}")

    client_secret = pmc["client_secret"]
    base_url = pmc["api_base_url"]
    record_id = pmc["record_id"]

    token = get_access_token(base_url, account_id, client_secret)
    properties = fetch_properties(base_url, token)

    print(f"[INFO] Retrieved {len(properties)} properties from {pmc['pms']} (Account: {account_id})")
    return save_to_airtable(properties, account_id, record_id)

def sync_all_pmcs():
    pmcs = fetch_pmc_lookup()
    total = 0

    for account_id in pmcs:
        print(f"[SYNC] 🔄 Syncing properties for {account_id}")
        try:
            total += sync_properties(account_id)
        except Exception as e:
            print(f"[ERROR] Failed to sync {account_id}: {e}")

    print(f"[SYNC] ✅ Total properties synced: {total}")
    return total

# Optional CLI test
if __name__ == "__main__":
    sync_all_pmcs()
