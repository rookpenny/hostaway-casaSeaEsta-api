# utils/prearrival_debug.py

from fastapi import APIRouter
from fastapi.responses import JSONResponse
import os
import requests

prearrival_debug_router = APIRouter()

@prearrival_debug_router.get("/api/debug/raw-prearrival")
def debug_raw_prearrival():
    try:
        AIRTABLE_TOKEN = os.getenv("AIRTABLE_API_KEY")
        BASE_ID = os.getenv("AIRTABLE_BASE_ID")
        TABLE_ID = "tbloNTWaJvuo71XQs"  # Your actual table ID

        url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
        headers = {
            "Authorization": f"Bearer {AIRTABLE_TOKEN}"
        }

        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Failed to fetch from Airtable",
                    "details": response.text
                }
            )

        return response.json()

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "Unexpected error", "details": str(e)}
        )
