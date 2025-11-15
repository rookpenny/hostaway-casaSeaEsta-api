from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
import os
import requests

prearrival_router = APIRouter()

def fetch_prearrival_options(phone: str) -> list:
    try:
        # Step 1: Check Guest Auth
        api_url = os.getenv("INTERNAL_API_URL", "http://localhost:10000")
        auth_url = f"{api_url}/checkGuestAuth"
        property = None

        auth_resp = requests.post(auth_url, json={"code": phone, "property": "casa-sea-esta"})  # property is required param
        if auth_resp.status_code != 200:
            return []

        guest = auth_resp.json()
        if not guest.get("verified"):
            return []

        # Step 2: Get linked property Airtable record ID
        property_id = guest.get("property_id")  # ⚠️ must match Airtable linked record ID

        if not property_id:
            return []

        # Step 3: Airtable fetch with filterByFormula
        AIRTABLE_TOKEN = os.getenv("AIRTABLE_API_KEY")
        BASE_ID = os.getenv("AIRTABLE_BASE_ID")
        TABLE_ID = "tbloNTWaJvuo71XQs"

        url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
        headers = {
            "Authorization": f"Bearer {AIRTABLE_TOKEN}"
        }

        params = {
            "filterByFormula": f"AND(active=TRUE(), FIND('{property_id}', ARRAYJOIN(Property)))"
        }

        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            return []

        records = response.json().get("records", [])
        options = []

        for record in records:
            fields = record.get("fields", {})
            options.append({
                "id": fields.get("ID"),
                "label": fields.get("Label"),
                "description": fields.get("Description"),
                "price": fields.get("Price")
            })

        return options

    except Exception as e:
        print("Error in fetch_prearrival_options:", str(e))
        return []

@prearrival_router.get("/api/prearrival-options")
def prearrival_options(phone: str = Query(...)):
    options = fetch_prearrival_options(phone)
    return {"options": options}
