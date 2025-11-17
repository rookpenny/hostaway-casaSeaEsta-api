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
