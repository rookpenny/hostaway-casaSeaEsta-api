import os
import requests
from datetime import datetime
from calendar import monthrange
from dotenv import load_dotenv

load_dotenv()

HOSTAWAY_BASE_URL = "https://api.hostaway.com/v1"
CLIENT_ID = os.getenv("HOSTAWAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("HOSTAWAY_CLIENT_SECRET")

def get_token():
    """Retrieve OAuth access token from Hostaway"""
    resp = requests.post(
        f"{HOSTAWAY_BASE_URL}/accessTokens",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "general"
        }
    )
    if not resp.ok:
        raise Exception("Hostaway authentication failed.")
    return resp.json().get("access_token")

def fetch_reservations(listing_id, token):
    """Get all reservations for the current month for a given listing ID"""
    today = datetime.today()
    year, month = today.year, today.month
    last_day = monthrange(year, month)[1]

    date_range_start = today.replace(day=1).strftime("%Y-%m-%d")
    date_range_end = today.replace(day=last_day).strftime("%Y-%m-%d")

    resp = requests.get(
        f"{HOSTAWAY_BASE_URL}/reservations",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "listingId": listing_id,
            "dateFrom": date_range_start,
            "dateTo": date_range_end
        }
    )

    if not resp.ok:
        raise Exception("Error fetching reservations from Hostaway")

    return resp.json().get("result", [])
