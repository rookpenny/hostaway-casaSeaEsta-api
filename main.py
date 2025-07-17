import os
import requests
from flask import Flask, jsonify, request, send_from_directory
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__, static_folder='.', static_url_path='')

CLIENT_ID = os.getenv("HOSTAWAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("HOSTAWAY_CLIENT_SECRET")
PROPERTY_LISTING_IDS = {"casa-sea-esta": "191357"}

@app.route("/")
def home():
    return send_from_directory('.', 'index.html')

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

@app.route("/api/guest")
def get_guest_info():
    slug = request.args.get("property")
    if slug not in PROPERTY_LISTING_IDS:
        return jsonify({"error": "Unknown property"}), 404

    token = get_token()
    if not token:
        return jsonify({"error": "Authentication failed"}), 401

    today = datetime.today().strftime("%Y-%m-%d")
    resp = requests.get(
        "https://api.hostaway.com/v1/reservations",
        headers={"Authorization": f"Bearer {token}"},
        params={"listingId": PROPERTY_LISTING_IDS[slug], "dateFrom": today, "dateTo": today}
    )
    print("Reservations status:", resp.status_code)
    data = resp.json()
    reservations = data.get("result", [])

    valid = [
        r for r in reservations
        if r.get("status") in {"new", "modified", "confirmed", "accepted"}
        and r.get("arrivalDate") <= today <= r.get("departureDate")
    ]

    if not valid:
        return jsonify({"message": "No active guest staying today"}), 404

    sel = max(valid, key=lambda r: r.get("updatedOn", ""))
    return jsonify({
        "guestName": sel.get("guestName"),
        "checkIn": sel.get("arrivalDate"),
        "checkInTime": sel.get("checkInTime"),
        "checkOut": sel.get("departureDate"),
        "checkOutTime": sel.get("checkOutTime"),
        "numberOfGuests": sel.get("numberOfGuests"),
        "notes": sel.get("comment"),
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=81)
