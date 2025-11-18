import os
import requests
from datetime import datetime

# Universal Airtable config
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PROPERTIES_TABLE_ID = "tblm0rEfkTDvsr5BU"  # Properties table
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"         # PMC table

def fetch_pmc_lookup():
    """Fetches all PMCs from Airtable and returns a lookup keyed by PMS Client ID."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch PMC records: {response.text}")

    lookup = {}
    for record in response.json().get("records", []):
        f = record.get("fields", {})
        pms = f.get("PMS Integration", "").strip().lower()
        client_id = str(f.get("PMS Client ID", "")).strip()
        client_secret = str(f.get("PMS Secret", "")).strip()

        if client_id and client_secret and pms:
            lookup[client_id] = {
                "record_id": record["id"],
                "client_secret": client_secret,
                "pms": pms
            }
    return lookup

def get_hostaway_access_token(client_id, client_secret):
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

def fetch_hostaway_properties(access_token):
    url = "https://api.hostaway.com/v1/listings"
    headers = {"Authorization": f"Bearer {access_token}"}

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Hostaway fetch failed: {response.text}")

    return response.json().get("result", [])

def save_properties_to_airtable(properties, account_id, pms_name, pmc_record_id=None):
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROPERTIES_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    synced_count = 0
    for prop in properties:
        payload = {
            "fields": {
                "PMS Property ID": str(prop.get("id")),
                "Property Name": prop.get("internalListingName", "Unnamed"),
                "PMS Client ID": account_id,
                "PMS Source": pms_name,
                "PMC": [pmc_record_id] if pmc_record_id else [],
                "Active": True,
                "Last Synced": datetime.utcnow().isoformat()
            }
        }

        response = requests.post(url, json=payload, headers=headers)
        if response.status_code in (200, 201):
            synced_count += 1
        else:
            print(f"[ERROR] Failed to save property {prop.get('internalListingName')}: {response.text}")
    return synced_count

def sync_properties_for_account(account_id):
    lookup = fetch_pmc_lookup()
    account = lookup.get(account_id)

    if not account:
        raise Exception(f"No PMC record found for PMS Client ID: {account_id}")

    pms = account["pms"]
    client_secret = account["client_secret"]
    pmc_record_id = account["record_id"]

    if pms == "hostaway":
        token = get_hostaway_access_token(account_id, client_secret)
        properties = fetch_hostaway_properties(token)
        count = save_properties_to_airtable(properties, account_id, "hostaway", pmc_record_id)
        print(f"[INFO] ✅ Saved {count} Hostaway properties.")
        return count

    raise NotImplementedError(f"PMS '{pms}' not supported yet.")

def sync_all_pmc_properties():
    total = 0
    for account_id in fetch_pmc_lookup().keys():
        try:
            print(f"[SYNC] 🔄 Syncing properties for: {account_id}")
            total += sync_properties_for_account(account_id)
        except Exception as e:
            print(f"[ERROR] Failed syncing {account_id}: {e}")
    print(f"[SYNC] ✅ Total properties synced: {total}")
    return total

# CLI runner
if __name__ == "__main__":
    sync_all_pmc_properties()
