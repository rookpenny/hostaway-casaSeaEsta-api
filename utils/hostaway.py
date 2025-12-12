import os
import requests
from datetime import datetime, timedelta
from calendar import monthrange
from functools import lru_cache
from dotenv import load_dotenv
#from utils.airtable import upsert_airtable_record
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


@lru_cache(maxsize=32)
def cached_token_for_pmc(client_id: str, client_secret: str) -> str:
    """Cache a token per PMC credentials."""
    return get_token_for_pmc(client_id, client_secret)



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


from typing import Optional, Tuple

def get_listing_overview(
    listing_id: str,
    client_id: str,
    client_secret: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Fetch a Hostaway listing once and return:
      - hero_image_url
      - address
      - city

    Uses per-PMC client_id / client_secret (same pattern as get_upcoming_phone_for_listing).
    """
    try:
        token = get_token_for_pmc(client_id, client_secret)

        resp = requests.get(
            f"{HOSTAWAY_BASE_URL}/listings/{listing_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"includeResources": 1},  # includes listingImages
            timeout=5,
        )
        if not resp.ok:
            print("[Hostaway] Error fetching listing:", resp.status_code, resp.text)
            return None, None, None

        data = resp.json()
        listing = data.get("result") or data.get("listing") or {}

        # ---- HERO IMAGE ----
        images = listing.get("listingImages") or []
        hero_url = None
        if images:
            primary = sorted(
                images,
                key=lambda img: img.get("sortOrder") if img.get("sortOrder") is not None else 9999,
            )[0]
            hero_url = primary.get("url")

        # ---- ADDRESS FIELDS ----
        address = listing.get("address")
        city = listing.get("city") or listing.get("cityName")

        return hero_url, address, city

    except Exception as e:
        print("[Hostaway] Error in get_listing_overview:", e)
        return None, None, None



def get_upcoming_phone_for_listing(
    listing_id: str,
    client_id: str,
    client_secret: str,
) -> tuple[str | None, str | None, str | None, str | None, str | None, str | None]:
    """
    Look up the phone for either:
      1) The CURRENT in-house reservation for a Hostaway listing (today between arrival & departure), or
      2) The NEXT upcoming reservation (arrival >= today, closest arrival date).

    Returns:
        (
            phone_last4,
            full_phone,
            reservation_id,
            guest_name,
            arrival_date,
            departure_date,
        )
        or (None, None, None, None, None, None) on failure / no match.
    """
    try:
        token = get_token_for_pmc(client_id, client_secret)
        reservations = fetch_reservations(listing_id, token)

        today = datetime.utcnow().date()

        current_stay = None
        current_arrival = None

        upcoming_res = None
        upcoming_days = None

        for r in reservations:
            # Try to get a usable phone field
            phone = (
                r.get("phone")
                or r.get("guestPhone")
                or r.get("guestPhoneNumber")
            )
            if not phone:
                continue

            checkin_str = r.get("arrivalDate")
            checkout_str = r.get("departureDate")
            if not checkin_str or not checkout_str:
                continue

            try:
                checkin = datetime.strptime(checkin_str, "%Y-%m-%d").date()
                checkout = datetime.strptime(checkout_str, "%Y-%m-%d").date()
            except Exception:
                continue

            # 1️⃣ Current in-house stay: today between arrival & departure (inclusive)
            if checkin <= today <= checkout:
                # If multiple, prefer the one with the earliest arrival
                if current_stay is None or checkin < current_arrival:
                    current_stay = r
                    current_arrival = checkin
                continue

            # 2️⃣ Future stay: arrival is after today
            days_until_checkin = (checkin - today).days
            if days_until_checkin >= 0:
                if upcoming_res is None or days_until_checkin < upcoming_days:
                    upcoming_res = r
                    upcoming_days = days_until_checkin

        # Prefer a current stay if we found one
        best_res = current_stay or upcoming_res
        if not best_res:
            return None, None, None, None, None, None

        full_phone = (
            best_res.get("phone")
            or best_res.get("guestPhone")
            or best_res.get("guestPhoneNumber")
        )
        if not full_phone:
            return None, None, None, None, None, None

        digits_only = "".join(ch for ch in full_phone if ch.isdigit())
        if len(digits_only) < 4:
            return None, None, None, None, None, None

        phone_last4 = full_phone[-4:]
        reservation_id = str(
            best_res.get("id")
            or best_res.get("reservationId")
            or ""
        )

        if not reservation_id:
            return None, None, None, None, None, None

        # 🔹 New fields
        guest_name = (
            best_res.get("guestName")
            or best_res.get("name")
            or None
        )
        arrival_date = best_res.get("arrivalDate")
        departure_date = best_res.get("departureDate")

        return phone_last4, full_phone, reservation_id, guest_name, arrival_date, departure_date

    except Exception as e:
        print("[Hostaway] Error in get_upcoming_phone_for_listing:", e)
        return None, None, None, None, None, None
