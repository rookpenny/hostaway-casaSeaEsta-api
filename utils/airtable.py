import requests
import os
import sys

# Load environment variables
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_PROPERTIES_TABLE_ID = "tblm0rEfkTDvsr5BU"
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"

# Debug startup logs
print("[DEBUG] AIRTABLE_API_KEY:", "✅ SET" if AIRTABLE_API_KEY else "❌ MISSING")
print("[DEBUG] AIRTABLE_BASE_ID:", AIRTABLE_BASE_ID or "❌ MISSING")

if AIRTABLE_API_KEY:
    print("[DEBUG] API Key starts with:", AIRTABLE_API_KEY[:6])
else:
    raise EnvironmentError("❌ Missing AIRTABLE_API_KEY")

if not AIRTABLE_BASE_ID:
    raise EnvironmentError("❌ Missing AIRTABLE_BASE_ID")

# Standard Airtable headers
HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json"
}

def fetch_pmcs():
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    print(f"[DEBUG] Fetching PMCs from {url}")
    print(f"[DEBUG] Headers: {HEADERS}")
    
    response = requests.get(url, headers=HEADERS)
    
    if response.status_code != 200:
        print(f"[ERROR] Failed to fetch PMCs: {response.status_code} - {response.text}")
        return []
    
    return response.json().get("records", [])

def upsert_airtable_record(table_id: str, record_id: str, fields: dict):
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{table_id}/{record_id}"
    payload = { "fields": fields }

    print(f"[DEBUG] PATCH to {url}")
    print(f"[DEBUG] Payload: {payload}")

    response = requests.patch(url, headers=HEADERS, json=payload)

    if response.status_code not in (200, 201):
        raise Exception(f"[ERROR] Airtable upsert failed: {response.status_code} - {response.text}")
    
    return response.json()

def save_properties_to_airtable(properties, pmc_id):
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROPERTIES_TABLE_ID}"
    print(f"[DEBUG] Saving properties to: {url}")

    for prop in properties:
        payload = {
            "fields": {
                **prop,
                "PMC": [pmc_id]
            }
        }

        print(f"[DEBUG] Sending property to Airtable: {payload}")
        res = requests.post(url, json=payload, headers=HEADERS)

        if res.status_code not in (200, 201):
            print(f"[ERROR] Failed to sync property: {res.status_code} - {res.text}")
        else:
            print(f"[DEBUG] ✅ Synced property {prop.get('Property Name', '[No Name]')}")

