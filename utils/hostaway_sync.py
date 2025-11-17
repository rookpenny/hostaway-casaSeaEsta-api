import os
import requests
from datetime import datetime

# Load credentials from environment
HOSTAWAY_CLIENT_ID = os.getenv("HOSTAWAY_CLIENT_ID")
HOSTAWAY_CLIENT_SECRET = os.getenv("HOSTAWAY_CLIENT_SECRET")
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PROPERTIES_TABLE_ID = "tblm0rEfkTDvsr5BU"  # Properties table ID
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"         # PMC table ID

def get_hostaway_access_token(client_id: str, client_secret: str):
    url = "https://api.hostaway.com/v1/accessTokens"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    response = requests.post(url, data=data, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Token request failed: {response.text}")

    return response.json()["access_token"]

def fetch_hostaway_properties(access_token):
    url = "https://api.hostaway.com/v1/listings"
    headers = {"Authorization": f"Bearer {access_token}"}

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Hostaway fetch failed: {response.text}")

    return response.json().get("result", [])

def fetch_pmc_lookup():
    """Builds a lookup of PMC credentials from Airtable using PMS Client ID and Secret."""
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

        if client_id and client_secret and pms.lower() == "hostaway":
            lookup[client_id] = {
                "record_id": record["id"],
                "client_secret": client_secret
            }

    return lookup

def save_to_airtable(properties, account_id):
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
                "Property Name": prop.get("internalListingName"),
                "Hostaway Property ID": str(prop.get("id")),
                "Hostaway Account ID": account_id,
                "PMC": [pmc_record_id] if pmc_record_id else [],
                "Notes": prop.get("name"),
                "Active": True,
                "Last Synced": datetime.utcnow().isoformat()
            }
        }

        res = requests.post(airtable_url, json=payload, headers=headers)
        if res.status_code in (200, 201):
            count += 1
        else:
            print(f"Failed to save property {prop.get('name')}: {res.text}")

    return count

def sync_hostaway_properties(account_id: str):
    # Treat account_id as the access token directly
    url = "https://api.hostaway.com/v1/properties"
    headers = {
        "Authorization": f"Bearer {account_id}"
    }

    print(f"[DEBUG] Requesting: {url}")
    response = requests.get(url, headers=headers)

    print(f"[DEBUG] Response: {response.status_code} - {response.text}")
    if response.status_code != 200:
        raise Exception(f"Failed to fetch properties: {response.text}")

    properties = response.json().get("properties", [])
    print(f"[INFO] Found {len(properties)} properties")

    # Insert properties into Airtable (as before)
    for prop in properties:
        airtable_payload = {
            "fields": {
                "Property Name": prop["name"],
                "Property ID": prop["id"],
                "PMS Client ID": account_id,
                "Active": True,
                "Sandy Enabled": True
            }
        }

        airtable_url = f"https://api.airtable.com/v0/{os.getenv('AIRTABLE_BASE_ID')}/tblXYZ123"
        airtable_headers = {
            "Authorization": f"Bearer {os.getenv('AIRTABLE_API_KEY')}",
            "Content-Type": "application/json"
        }

        airtable_res = requests.post(airtable_url, headers=airtable_headers, json=airtable_payload)
        if not airtable_res.ok:
            print(f"[ERROR] Failed to insert property: {airtable_res.text}")

    return len(properties)



def sync_all_pmc_properties():
    pmc_lookup = fetch_pmc_lookup()
    total = 0

    for account_id in pmc_lookup.keys():
        print(f"[SYNC] 🔄 Syncing properties for PMC: {account_id}")
        total += sync_hostaway_properties(account_id)

    print(f"[SYNC] ✅ Total properties synced across all PMCs: {total}")
    return total

# Optional for local test
if __name__ == "__main__":
    # To sync all PMCs
    sync_all_pmc_properties()

    # OR to sync one PMC manually
    # sync_hostaway_properties("your-account-id-here")
