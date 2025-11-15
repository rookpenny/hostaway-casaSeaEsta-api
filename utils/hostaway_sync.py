import os
import requests

HOSTAWAY_API_KEY = os.getenv("HOSTAWAY_API_KEY")
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PROPERTIES_TABLE_ID = "tblXXXXXXXXXXXXXX"  # Replace with your actual table ID

# STEP 1 – Fetch Properties from Hostaway
def fetch_hostaway_properties():
    url = "https://api.hostaway.com/v1/listings"
    headers = {"Authorization": f"Bearer {HOSTAWAY_API_KEY}"}
    
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Hostaway fetch failed: {response.text}")
    
    return response.json()["result"]["listings"]

# STEP 2 – Save Each Property into Airtable
def save_to_airtable(properties):
    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROPERTIES_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    for prop in properties:
        payload = {
            "fields": {
                "Property Name": prop["name"],
                "Hostaway ID": str(prop["id"]),
                "Hostaway Account": str(prop["accountId"]),
                "Active": True  # Optional, default to True
            }
        }

        res = requests.post(airtable_url, json=payload, headers=headers)
        if res.status_code not in (200, 201):
            print(f"Failed to save property {prop['name']}: {res.text}")

# MAIN RUNNER
def sync_hostaway_properties():
    print("Fetching properties from Hostaway...")
    properties = fetch_hostaway_properties()
    print(f"Fetched {len(properties)} properties.")

    print("Saving to Airtable...")
    save_to_airtable(properties)
    print("Done.")

if __name__ == "__main__":
    sync_hostaway_to_airtable()
