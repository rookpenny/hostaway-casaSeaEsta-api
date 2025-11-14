from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
import os
import requests

prearrival_router = APIRouter()

def fetch_prearrival_options(phone: str) -> list:
    try:
        AIRTABLE_TOKEN = os.getenv("AIRTABLE_API_KEY")
        BASE_ID = os.getenv("AIRTABLE_BASE_ID")
        TABLE_ID = "tbloNTWaJvuo71XQs"  # Your correct table ID

        url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
        headers = {
            "Authorization": f"Bearer {AIRTABLE_TOKEN}"
        }

        response = requests.get(url, headers=headers)
        print(response.json())  # 🔍 Debug Airtable response

        if response.status_code != 200:
            return []

        records = response.json().get("records", [])
        options = []

        for record in records:
            fields = record.get("fields", {})
            print(fields)  # 🔍 Inspect each row from Airtable

            if not fields.get("Active"):
                continue
            if fields.get("Property") != "Casa Sea Esta":
                continue

            options.append({
                "id": fields.get("ID"),
                "label": fields.get("Label"),
                "description": fields.get("Description"),
                "price": fields.get("Price")
            })

        return options

    except Exception as e:
        print(f"Error fetching prearrival options: {e}")
        return []

@prearrival_router.get("/api/prearrival-options")
def prearrival_options(
    phone: str = Query(...),
    property: str = Query("Casa Sea Esta")  # Default property name
):
    options = fetch_prearrival_options(phone, property)
    return {"options": options}
