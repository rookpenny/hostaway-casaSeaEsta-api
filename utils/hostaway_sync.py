import os
import requests

# Load credentials from environment
HOSTAWAY_CLIENT_ID = os.getenv("HOSTAWAY_CLIENT_ID")
HOSTAWAY_CLIENT_SECRET = os.getenv("HOSTAWAY_CLIENT_SECRET")
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PROPERTIES_TABLE_ID = "tblm0rEfkTDvsr5BU"  # Properties table ID
AIRTABLE_PMC_TABLE_NAME = "PMC"  # PMC Table Name (not the ID)

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
    if isinstance(data, dict) and "result" in data and isinstance(data["result"], list):
        return data["result"]

    raise Exception(f"Unexpected data format from Hostaway: {data}")

def fetch_pmc_lookup():
    """Fetch PMC records from Airtable and build a lookup by Hostaway Account ID."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_NAME}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch PMC records: {response.text}")

    records = response.json().get("records", [])
    lookup = {}
    for record in records:
        fields = record.get("fields", {})
        account_id = fields.get("Hostaway Account ID")
        if account_id:
            lookup[str(account_id)] = record["id"]
    return lookup

def save_to_airtable(properties):
    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROPERTIES_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    # Fetch PMC link lookup
    pmc_lookup = fetch_pmc_lookup()
    count = 0

    # Fetch existing properties to check for upserts
    existing_records_url = airtable_url + "?fields[]=Hostaway%20Property%20ID"
    existing_records = requests.get(existing_records_url, headers=headers).json().get("records", [])
    existing_map = {record["fields"].get("Hostaway Property ID"): record["id"] for record in existing_records if "Hostaway Property ID" in record.get("fields", {})}

    for prop in properties:
        property_id = str(prop.get("id"))
        account_id = str(prop.get("client_id"))
        pmc_record_id = pmc_lookup.get(account_id)

        fields = {
            "Property Name": prop.get("internalListingName"),
            "Hostaway Property ID": property_id,
            "Hostaway Account ID": account_id,
            "PMC": [pmc_record_id] if pmc_record_id else [],
            "Notes": prop.get("name"),
            "Active": True
        }

        if property_id in existing_map:
            # UPDATE
            record_id = existing_map[property_id]
            url = f"{airtable_url}/{record_id}"
            res = requests.patch(url, json={"fields": fields}, headers=headers)
        else:
            # CREATE
            res = requests.post(airtable_url, json={"fields": fields}, headers=headers)

        if res.status_code in (200, 201):
            count += 1
        else:
            print(f"Failed to sync property {prop.get('name')}: {res.text}")

    return count

def sync_hostaway_properties():
    access_token = get_hostaway_access_token()
    properties = fetch_hostaway_properties(access_token)
    return save_to_airtable(properties)

# Run for local testing
if __name__ == "__main__":
    synced = sync_hostaway_properties()
    print(f"✅ Synced {synced} properties to Airtable")
