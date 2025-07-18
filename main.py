import os
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime
from dotenv import load_dotenv
from calendar import monthrange

load_dotenv()
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

CLIENT_ID = os.getenv("HOSTAWAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("HOSTAWAY_CLIENT_SECRET")
PROPERTY_LISTING_IDS = {"casa-sea-esta": "256853"}

def get_token():
    resp = requests.post(
        "https://api.hostaway.com/v1/accessTokens",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "general"
        }
    )
    print("Token status:", resp.status_code, resp.text[:200])
    return resp.json().get("access_token") if resp.ok else None

@app.route("/")
def home():
    return jsonify({"message": "Welcome to Casa Sea Esta API!"}), 200

@app.route("/api/guest")
def get_guest_info():
    slug = request.args.get("property")
    if slug not in PROPERTY_LISTING_IDS:
        return jsonify({"error": "Unknown property"}), 404

    token = get_token()
    print("Token used:", token)
    if not token:
        return jsonify({"error": "Authentication failed"}), 401

    today = datetime.today().strftime("%Y-%m-%d")
    year = datetime.today().year
    month = datetime.today().month
    last_day = monthrange(year, month)[1]
    date_range_start = datetime.today().replace(day=1).strftime("%Y-%m-%d")
    date_range_end = datetime.today().replace(day=last_day).strftime("%Y-%m-%d")

    try:
        resp = requests.get(
            "https://api.hostaway.com/v1/reservations",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "listingId": PROPERTY_LISTING_IDS[slug],
                "dateFrom": date_range_start,
                "dateTo": date_range_end
            }
        )
        print("Reservations status:", resp.status_code)
        data = resp.json()
    except Exception as e:
        print("Failed to fetch or parse reservations:", str(e))
        return jsonify({"error": "Upstream Hostaway error"}), 502

    reservations = data.get("result", [])

    print("Today's date:", today)
    for r in reservations:
        print("Reservation:", r.get("guestName"), r.get("arrivalDate"), r.get("departureDate"), r.get("status"))

    valid = [
        r for r in reservations
        if r.get("status") in {"new", "modified", "confirmed", "accepted"}
        and r.get("arrivalDate") <= today <= r.get("departureDate")
    ]

    if not valid:
        return jsonify({"message": "No active guest staying today"}), 404

    sel = max(valid, key=lambda r: r.get("updatedOn", ""))

    selected = {
        "guestName": sel.get("guestName"),
        "checkIn": sel.get("arrivalDate"),
        "checkInTime": str(sel.get("checkInTime", 16)),
        "checkOut": sel.get("departureDate"),
        "checkOutTime": str(sel.get("checkOutTime", 10)),
        "numberOfGuests": str(sel.get("numberOfGuests")),
        "notes": sel.get("comment", "")
    }

    print("Selected reservation:", selected)
    return jsonify(selected), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
