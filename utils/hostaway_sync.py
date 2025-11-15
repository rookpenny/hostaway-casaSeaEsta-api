import os
import requests

# Load environment variables
HOSTAWAY_CLIENT_ID = os.getenv("HOSTAWAY_CLIENT_ID")
HOSTAWAY_CLIENT_SECRET = os.getenv("HOSTAWAY_CLIENT_SECRET")
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PROPERTIES_TABLE_ID = "tblm0rEfkTDvsr5BU"  # Replace this with your actual table ID

# === Step 1: Generate Access Token from Hostaway ===
def get_hostaway_access_token():
    url = "https://api.hostaway.com/v1/accessTokens"
    payload = {
        "clientId": HOSTAWAY_CLIENT_ID,
        "secret": HOSTAWAY_CLIENT_SECRET
    }

    response = requests.post(url, json=payload)
    if response.status_code != 200:
        raise Exception(f"Token request failed: {response.text}")

    return response.json()["access_token"]

# === Step 2: Fetch Property Listings from Hostaway ===
def fetch_hostaway_properties(access_token):
    url = "https://api.hostaway.com/v1/listings"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Hostaway fetch failed: {response.text}")

    return response.json()["result"]["listings"]

# === Step 3: Save to Airtable ===
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
                "Active": True
            }
        }

        res = requests.post(airtable_url, json=payload, headers=headers)
        if res.status_code not in (200, 201):
            print(f"❌ Failed to save property {prop['name']}: {res.text}")
        else:
            print(f"✅ Saved: {prop['name']}")

# === MAIN Sync Function ===
def sync_hostaway_properties():
    print("🔑 Getting Hostaway token...")
    token = get_hostaway_access_token()

    print("📦 Fetching properties from Hostaway...")
    properties = fetch_hostaway_properties(token)
    print(f"✅ Fetched {len(properties)} properties.")

    print("💾 Saving to Airtable...")
    save_to_airtable(properties)
    print("🎉 Done!")

if __name__ == "__main__":
    sync_hostaway_properties()
