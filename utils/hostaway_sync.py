import os
import requests

# Load credentials from environment
HOSTAWAY_CLIENT_ID = os.getenv("HOSTAWAY_CLIENT_ID")
HOSTAWAY_CLIENT_SECRET = os.getenv("HOSTAWAY_CLIENT_SECRET")
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PROPERTIES_TABLE_ID = "tblm0rEfkTDvsr5BU"  # Replace with your actual table ID


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
    print("DEBUG: Full response:", data)

    if isinstance(data, dict) and "result" in data and isinstance(data["result"], list):
        return data["result"]

    raise Exception(f"Unexpected data format from Hostaway: {data}")


def save_to_airtable(properties):
    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROPERTIES_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    count = 0
    for prop in properties:
        payload = {
            "fields": {
                "Property Name": prop.get("name"),
                "Hostaway ID": str(prop.get("id")),
                "Hostaway Account": str(prop.get("accountId")),
                "Active": True
            }
        }

        res = requests.post(airtable_url, json=payload, headers=headers)
        if res.status_code in (200, 201):
            count += 1
        else:
            print(f"Failed to save property {prop.get('name')}: {res.text}")

    return count


def sync_hostaway_properties():
    access_token = get_hostaway_access_token()
    properties = fetch_hostaway_properties(access_token)
    return save_to_airtable(properties)


# Optional: for local testing
if __name__ == "__main__":
    synced = sync_hostaway_properties()
    print(f"✅ Synced {synced} properties to Airtable")
