import os
import requests

AIRTABLE_TOKEN = os.getenv("AIRTABLE_API_KEY")

def upsert_airtable_record(base_id, table_name, unique_field, record_data):
    url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_TOKEN}",
        "Content-Type": "application/json"
    }

    # Search for existing record
    filter_formula = f"{{{unique_field}}} = '{record_data[unique_field]}'"
    search_url = f"{url}?filterByFormula={requests.utils.quote(filter_formula)}"
    response = requests.get(search_url, headers=headers)
    records = response.json().get("records", [])

    if records:
        record_id = records[0]["id"]
        update_url = f"{url}/{record_id}"
        requests.patch(update_url, headers=headers, json={"fields": record_data})
    else:
        requests.post(url, headers=headers, json={"fields": record_data})
