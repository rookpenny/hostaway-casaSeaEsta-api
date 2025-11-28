import os
import requests
from datetime import datetime, timedelta
from calendar import monthrange
from functools import lru_cache
from dotenv import load_dotenv
from utils.airtable import upsert_airtable_record
from typing import Optional, Tuple

HOSTAWAY_API_KEY = os.getenv("HOSTAWAY_API_KEY")
HOSTAWAY_ACCOUNT_ID = os.getenv("HOSTAWAY_ACCOUNT_ID")

load_dotenv()

HOSTAWAY_BASE_URL = "https://api.hostaway.com/v1"
CLIENT_ID = os.getenv("HOSTAWAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("HOSTAWAY_CLIENT_SECRET")

def get_token_for_pmc(client_id: str, client_secret: str) -> str:
    """Get a Hostaway access token using *per PMC* credentials."""
    resp = requests.post(
        f"{HOSTAWAY_BASE_URL}/accessTokens",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "general",
        },
    )
    if not resp.ok:
        print("[Hostaway] Auth failed:", resp.status_code, resp.text)
        raise Exception("Hostaway authentication failed.")
    return resp.json().get("access_token")


@lru_cache(maxsize=1)
def cached_token():
    """Return a cached token to avoid repeat API calls."""
    return get_token()


def fetch_reservations(listing_id: str, token: str):
    """
    Fetch reservations for a listing in a rolling window:
    30 days in the past to 60 days in the future.
    This covers:
      - current in-house stays that started last month
      - upcoming reservations in the near future
    """
    today = datetime.utcnow().date()
    date_from = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    date_to = (today + timedelta(days=60)).strftime("%Y-%m-%d")

    resp = requests.get(
        f"{HOSTAWAY_BASE_URL}/reservations",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "listingId": listing_id,
            "dateFrom": date_from,
            "dateTo": date_to,
        },
    )
    if not resp.ok:
        print("[Hostaway] Error fetching reservations:", resp.status_code, resp.text)
        raise Exception("Error fetching reservations from Hostaway")

    data = resp.json()
    result = data.get("result", [])
    print(
        f"[Hostaway] fetched {len(result)} reservations for listing {listing_id} "
        f"between {date_from} and {date_to}"
    )
    return result

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



def get_upcoming_phone_for_listing(
    listing_id: str,
    client_id: str,
    client_secret: str,
) -> tuple[str | None, str | None, str | None]:
    """
    Look up the next upcoming reservation for a Hostaway listing.

    Returns:
        (phone_last4, full_phone, reservation_id)
        or (None, None, None) on failure / no match.
    """
    try:
        token = get_token_for_pmc(client_id, client_secret)
        reservations = fetch_reservations(listing_id, token)

        today = datetime.utcnow().date()

        best_res = None
        best_days = None

        for r in reservations:
            phone = r.get("phone", "")
            if not phone:
                continue

            checkin_str = r.get("arrivalDate")
            if not checkin_str:
                continue

            try:
                checkin = datetime.strptime(checkin_str, "%Y-%m-%d").date()
            except Exception:
                continue

            days_until_checkin = (checkin - today).days
            if 0 <= days_until_checkin <= 20:
                if best_res is None or days_until_checkin < best_days:
                    best_res = r
                    best_days = days_until_checkin

        if not best_res:
            return None, None, None

        full_phone = best_res.get("phone")
        if not full_phone:
            return None, None, None

        phone_last4 = full_phone[-4:]
        reservation_id = str(best_res.get("id") or best_res.get("reservationId") or "")

        return phone_last4, full_phone, reservation_id

    except Exception as e:
        print("[Hostaway] Error in get_upcoming_phone_for_listing:", e)
        return None, None, None
