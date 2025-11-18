import os
import requests
from datetime import datetime

# ENV Vars
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PROPERTIES_TABLE_ID = "tblm0rEfkTDvsr5BU"
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"

# 🔑 Fetch OAuth token from Hostaway
def get_hostaway_access_token(client_id: str, client_secret: str) -> str:
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

# 🔁 Fetch properties from Hostaway
def fetch_hostaway_properties(token: str):
    url = "https://api.hostaway.com/v1/listings"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        raise Exception(f"Hostaway fetch failed: {response.text}")

    return response.json().get("result", [])

# 📥 Pull PMC credentials from Airtable
def fetch_pmc_lookup():
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        raise Exception(f"Failed to fetch PMC records: {response.text}")

    records = response.json().get("records", [])
    lookup = {}

    for record in records:
        fields = record.get("fields", {})
        if fields.get("PMS Integration", "").lower() != "hostaway":
            continue

        hostaway_account_id = str(fields.get("Hostaway Account ID", "")).strip()
        client_id = str(fields.get("PMS Client ID", "")).strip()
        client_secret = str(fields.get("PMS Secret", "")).strip()

        if hostaway_account_id and client_id and client_secret:
            lookup[hostaway_account_id] = {
                "record_id": record["id"],
                "client_id": client_id,
                "client_secret": client_secret
            }

    return lookup

# 💾 Save properties to Airtable
def save_to_airtable(properties, hostaway_account_id, pmc_record_id):
    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROPERTIES_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }
    count = 0

    for prop in properties:
        payload = {
            "fields": {
                "Property Name": prop.get("internalName"),
                "Hostaway Property ID": str(prop.get("id")),
                "Hostaway Account ID": hostaway_account_id,
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
            print(f"[ERROR] Failed to save property {prop.get('name')}: {res.text}")

    return count

# 🔄 Sync a specific PMC by Hostaway Account ID
def sync_hostaway_properties(account_id: str):
    pmc_lookup = fetch_pmc_lookup()
    pmc = pmc_lookup.get(account_id)

    if not pmc:
        raise Exception(f"No PMC found for Hostaway Account ID {account_id}")

    client_id = pmc["client_id"]
    client_secret = pmc["client_secret"]
    pmc_record_id = pmc["record_id"]

    print(f"[INFO] Syncing for PMC with Hostaway Account ID: {account_id}")

    token = get_hostaway_access_token(client_id, client_secret)
    properties = fetch_hostaway_properties(token)

    print(f"[INFO] Retrieved {len(properties)} properties from Hostaway")

    count = save_to_airtable(properties, account_id, pmc_record_id)
    print(f"[INFO] ✅ Saved {count} properties to Airtable")

    return count

# 🔁 Sync all PMCs (used in /admin/sync-all)
def sync_all_pmcs():
    pmc_lookup = fetch_pmc_lookup()
    total = 0

    for account_id in pmc_lookup.keys():
        try:
            total += sync_hostaway_properties(account_id)
        except Exception as e:
            print(f"[ERROR] Skipped syncing {account_id}: {e}")

    print(f"[SYNC COMPLETE] ✅ Total properties synced: {total}")
    return total

def sync_all_pmc_properties():
    pmc_lookup = fetch_pmc_lookup()
    total = 0

    for account_id in pmc_lookup.keys():
        print(f"[SYNC] 🔄 Syncing properties for PMC: {account_id}")
        total += sync_hostaway_properties(account_id)

    print(f"[SYNC] ✅ Total properties synced across all PMCs: {total}")
    return total

# 🧪 Local test
if __name__ == "__main__":
    sync_all_pmcs()
    # OR test one:
    # sync_hostaway_properties("63652")
