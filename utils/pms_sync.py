import os
import requests
from datetime import datetime
from utils.github_sync import sync_pmc_to_github
from dotenv import load_dotenv
load_dotenv()

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

    if headers["Content-Type"] == "application/json":
        response = requests.post(token_url, json=payload, headers=headers)
    else:
        response = requests.post(token_url, data=payload, headers=headers)

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


def save_to_airtable(properties, account_id, pmc_record_id, pms):
    """Write fetched property records to Airtable."""
    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROPERTIES_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    count = 0
    updated_files = {}

    for prop in properties:
        prop_id = str(prop.get("id"))
        name = prop.get("internalListingName") or prop.get("name")

        # ✅ Create folder and collect paths
        base_dir = ensure_pmc_structure(pmc_name=account_id, property_id=prop_id, property_name=name)
        config_path = os.path.join(base_dir, "config.json")
        manual_path = os.path.join(base_dir, "manual.txt")

        updated_files[os.path.relpath(config_path)] = config_path
        updated_files[os.path.relpath(manual_path)] = manual_path

        payload = {
            "fields": {
                "Property Name": name,
                "PMS Property ID": prop_id,
                "PMC Record ID": [pmc_record_id],
                "PMS Integration": pms,
                "Sync Enabled": True,
                "Last Synced": datetime.utcnow().isoformat(),
                "Sandy Enabled": True,
                "Data Folder Path": base_dir  # ✅ Sets the path in Airtable
            }
        }

        res = requests.post(airtable_url, json=payload, headers=headers)
        if res.status_code in (200, 201):
            count += 1
        else:
            print(f"[ERROR] Failed to save property {name}: {res.text}")

    # ✅ Push created/updated files to GitHub
    try:
        sync_pmc_to_github(account_id, updated_files)
    except Exception as e:
        print(f"[GITHUB] ⚠️ Failed to push PMC {account_id} to GitHub: {e}")

    return count




def sync_properties(account_id: str):
    """Sync a single PMC by account ID and push created folders/files to GitHub."""
    pmcs = fetch_pmc_lookup()
    print(f"[DEBUG] Fetched PMCs: {list(pmcs.keys())}")
    if account_id not in pmcs:
        raise Exception(f"PMC not found for account ID: {account_id}")

    pmc = pmcs[account_id]
    token = get_access_token(
        pmc["client_id"],
        pmc["client_secret"],
        pmc["base_url"],
        pmc["pms"]
    )
    properties = fetch_properties(token, pmc["base_url"], pmc["pms"])

    # ⬇️ Get property folders + file paths from Airtable save
    results = save_to_airtable(properties, account_id, pmc["record_id"], pmc["pms"])

    # 🔁 GitHub Push: push each property folder separately
    try:
        for res in results:
            sync_pmc_to_github(res["folder"], res["files"])
    except Exception as e:
        print(f"[GITHUB] ⚠️ Failed to push PMC {account_id} to GitHub: {e}")

    print(f"[SYNC] ✅ Saved {len(results)} properties for {account_id}")
    return len(results)

    
    


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

def ensure_pmc_structure(pmc_name: str, property_id: str, property_name: str):
    # Clean folder names for filesystem safety
    safe_pmc_name = pmc_name.replace(" ", "_")
    safe_prop_name = property_name.replace(" ", "_").replace("/", "-")
    base_dir = f"data/{safe_pmc_name}/{property_id}"

    os.makedirs(base_dir, exist_ok=True)

    # Create empty config and manual if missing
    config_path = os.path.join(base_dir, "config.json")
    manual_path = os.path.join(base_dir, "manual.txt")

    if not os.path.exists(config_path):
        with open(config_path, "w") as f:
            f.write("{}")

    if not os.path.exists(manual_path):
        with open(manual_path, "w") as f:
            f.write("")

    return base_dir




# For local test
if __name__ == "__main__":
    sync_all_pmcs()
