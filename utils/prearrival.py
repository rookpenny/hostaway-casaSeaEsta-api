from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
import os
import requests

prearrival_router = APIRouter()

@prearrival_router.get("/api/prearrival-options")
def prearrival_options(phone: str = Query(...)):
    try:
        # Consolidated Airtable API access (now "HostScout")
        AIRTABLE_TOKEN = os.getenv("AIRTABLE_API_KEY")  # 🔁 Use consolidated env var
        BASE_ID = os.getenv("AIRTABLE_BASE_ID")         # 🔁 Use consolidated env var
        TABLE_ID = "tblviNlbgLbdEalOj"

        url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
        headers = {
            "Authorization": f"Bearer {AIRTABLE_TOKEN}"
        }

        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return JSONResponse(
                status_code=500,
                content={"error": "Failed to fetch from Airtable", "details": response.text}
            )

        records = response.json().get("records", [])
        options = []

        for record in records:
            fields = record.get("fields", {})
            if not fields.get("active"):
                continue

            options.append({
                "id": fields.get("id"),
                "label": fields.get("label"),
                "description": fields.get("description"),
                "price": fields.get("price")
            })

        return {"options": options}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "Unexpected error", "details": str(e)}
        )
