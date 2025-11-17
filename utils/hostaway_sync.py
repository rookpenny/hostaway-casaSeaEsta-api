import os
import requests

# Load credentials from environment
HOSTAWAY_CLIENT_ID = os.getenv("HOSTAWAY_CLIENT_ID")
HOSTAWAY_CLIENT_SECRET = os.getenv("HOSTAWAY_CLIENT_SECRET")
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PROPERTIES_TABLE_ID = "tblm0rEfkTDvsr5BU"  # Properties table ID
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"         # PMC table ID

def get_hostaway_access_token():
    url = "https://api.hostaway.com/v1/accessTokens"
    data = {
        "grant_type": "client_credentials",
        "client_id": HOSTAWAY_CLIENT_ID,
        "client_secret": HOSTAWAY_CLIENT_SECRET,
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

    data = response.json()
    print("[DEBUG] Raw Hostaway listings data:")
    print(data)

    return data.get("result", [])

def fetch_pmc_lookup():
    """Fetch PMC records from Airtable and build a lookup by Hostaway Account ID."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch PMC records: {response.text}")

    records = response.json().get("records", [])
    lookup = {}
    for record in records:
        fields = record.get("fields", {})
        account_id = str(fields.get("Hostaway Account ID")).strip()
        if account_id and record.get("id"):
            lookup[account_id] = record["id"]
    return lookup

def save_to_airtable(properties):
    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROPERTIES_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    pmc_lookup = fetch_pmc_lookup()
    count = 0

    for prop in properties:
        account_id = str(prop.get("client_id", "")).strip()
        pmc_record_id = pmc_lookup.get(account_id)

        print(f"Saving property for account_id: {account_id}")
        print(f"PMC Record ID: {pmc_record_id}")

        payload = {
            "fields": {
                "Property Name": prop.get("internalListingName"),
                "Hostaway Property ID": str(prop.get("id")),
                "Hostaway Account ID": account_id,
                "PMC": [pmc_record_id] if pmc_record_id else [],
                "Notes": prop.get("name"),
                "Active": True
            }
        }

        res = requests.post(airtable_url, json=payload, headers=headers)
        if res.status_code in (200, 201):
            count += 1
        else:
            print(f"Failed to save property {prop.get('name')}: {res.text}")

    return count

def sync_hostaway_properties(account_id: str):
    access_token = get_hostaway_access_token()
    properties = fetch_hostaway_properties(access_token)

    import json
    print("[DEBUG] First property raw dump:")
    if properties:
        print(json.dumps(properties[0], indent=2))
    else:
        print("No properties returned.")
        return 0  # early return if no data

    print(f"[DEBUG] Total properties fetched from Hostaway: {len(properties)}")
    print(f"[DEBUG] Filtering for Hostaway Account ID: {account_id}")

    # Extra debug: show each property's accountId and client_id
    for p in properties:
        print(f"  - Listing ID: {p.get('id')}, accountId: {p.get('accountId')}, client_id: {p.get('client_id')}")

    # Use correct field for filtering (we’ll update this after your next debug output)
    filtered = [p for p in properties if str(p.get("accountId")) == str(account_id)]
    print(f"[DEBUG] ✅ {len(filtered)} properties matched for account ID {account_id}")

    return save_to_airtable(filtered)

def sync_all_pmc_properties():
    pmc_lookup = fetch_pmc_lookup()
    total = 0

    for account_id in pmc_lookup.keys():
        print(f"[SCHEDULER] Syncing properties for account: {account_id}")
        total += sync_hostaway_properties(account_id)

    print(f"[SCHEDULER] ✅ Total properties synced across all PMCs: {total}")
    return total

# Optional for local testing
if __name__ == "__main__":
    synced = sync_hostaway_properties()
    print(f"✅ Synced {synced} properties to Airtable")
