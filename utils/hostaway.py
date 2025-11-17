import os
import requests
from datetime import datetime
from calendar import monthrange
from functools import lru_cache
from dotenv import load_dotenv
from utils.airtable import upsert_airtable_record

HOSTAWAY_API_KEY = os.getenv("HOSTAWAY_API_KEY")
HOSTAWAY_ACCOUNT_ID = os.getenv("HOSTAWAY_ACCOUNT_ID")
AIRTABLE_PROPERTIES_TABLE = "Properties"  # Airtable table name
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

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

@lru_cache(maxsize=1)
def cached_token():
    """Return a cached token to avoid repeat API calls."""
    return get_token()

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

def calculate_extra_nights(next_start_date):
    """
    Given the start date of the next reservation (YYYY-MM-DD),
    return number of nights available from today until then.
    If no future reservation exists, return 'open-ended'.
    """
    if not next_start_date:
        return "open-ended"

    try:
        today = datetime.utcnow().date()
        next_date = datetime.strptime(next_start_date, "%Y-%m-%d").date()
        delta = (next_date - today).days
        return max(0, delta)
    except Exception as e:
        print(f"Error calculating extra nights: {e}")
        return 0

def find_upcoming_guest_by_code(code: str, slug: str) -> dict | None:
    """
    Match a guest by the last 4 digits of phone number and return their upcoming reservation.
    """
    from utils.config import load_property_config  # import here to avoid circular imports

    try:
        config = load_property_config(slug)
        listing_id = config["listing_id"]
        property_name = config.get("property_name", slug.replace("-", " ").title())

        token = cached_token()
        reservations = fetch_reservations(listing_id, token)

        today = datetime.today().date()

        for r in reservations:
            phone = r.get("phone", "")
            if not phone or not phone.endswith(code):
                continue

            checkin_str = r.get("arrivalDate")
            if not checkin_str:
                continue

            checkin = datetime.strptime(checkin_str, "%Y-%m-%d").date()
            days_until_checkin = (checkin - today).days

            if 0 <= days_until_checkin <= 20:
                return {
                    "name": r.get("guestName", "Guest"),
                    "phone": phone,
                    "property": property_name,
                    "checkin_date": checkin_str,
                    "checkout_date": r.get("departureDate")
                }

    except Exception as e:
        print(f"[Guest Lookup] Error in find_upcoming_guest_by_code: {e}")
        return None


def get_hostaway_properties():
    url = "https://api.hostaway.com/v1/properties"
    headers = {
        "Authorization": f"Bearer {HOSTAWAY_API_KEY}",
        "Content-Type": "application/json"
    }

    params = {
        "accountId": HOSTAWAY_ACCOUNT_ID
    }

    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch Hostaway properties: {response.text}")

    return response.json().get("result", [])

