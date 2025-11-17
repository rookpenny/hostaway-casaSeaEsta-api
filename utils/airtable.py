import requests
import os

AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_PROPERTIES_TABLE_ID = "tblm0rEfkTDvsr5BU"
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"

def fetch_pmcs():
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}
    return requests.get(url, headers=headers).json().get("records", [])

def upsert_airtable_record(table_id: str, record_id: str, fields: dict):
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{table_id}/{record_id}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "fields": fields
    }

    response = requests.patch(url, headers=headers, json=payload)
    if response.status_code not in (200, 201):
        raise Exception(f"[ERROR] Airtable upsert failed: {response.text}")
    return response.json()
    
def save_properties_to_airtable(properties, pmc_id):
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROPERTIES_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    for prop in properties:
        payload = {
            "fields": {
                **prop,
                "PMC": [pmc_id]
            }
        }
        res = requests.post(url, json=payload, headers=headers)
        if res.status_code not in (200, 201):
            print(f"[ERROR] Failed to sync property: {res.text}")
