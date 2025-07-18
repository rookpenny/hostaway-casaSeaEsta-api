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
    try:
        slug = request.args.get("property")
        if slug not in PROPERTY_LISTING_IDS:
            return jsonify({"error": "Unknown property"}), 404

        token = get_token()
        print("Token used:", token)
        if not token:
            return jsonify({"error": "Authentication failed"}), 401

        now = datetime.now()
        today = now.date()
        current_time = now.time()
        year = today.year
        month = today.month
        last_day = monthrange(year, month)[1]
        date_range_start = today.replace(day=1).strftime("%Y-%m-%d")
        date_range_end = today.replace(day=last_day).strftime("%Y-%m-%d")

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
        reservations = data.get("result", [])

        # DEBUG: Print all reservations
        print("\n=== RAW RESERVATIONS RECEIVED ===")
        for r in reservations:
            print({
                "guestName": r.get("guestName"),
                "arrivalDate": r.get("arrivalDate"),
                "departureDate": r.get("departureDate"),
                "status": r.get("status"),
                "checkInTime": r.get("checkInTime"),
                "checkOutTime": r.get("checkOutTime")
            })
        print("=== END RAW RESERVATIONS ===\n")

        valid_reservations = [
            r for r in reservations
            if r.get("status") in {"new", "modified", "confirmed", "accepted"}
        ]

        selected = None

        for r in valid_reservations:
            try:
                arrival = datetime.strptime(r.get("arrivalDate"), "%Y-%m-%d").date()
                departure = datetime.strptime(r.get("departureDate"), "%Y-%m-%d").date()
                checkin_hour = int(r.get("checkInTime", 16))
                checkin_time = datetime.combine(arrival, datetime.min.time()).replace(hour=checkin_hour).time()
                checkout_hour = int(r.get("checkOutTime", 10))
                checkout_time = datetime.combine(departure, datetime.min.time()).replace(hour=checkout_hour).time()
            except Exception as e:
                print("ERROR parsing reservation:", e, r)
                continue

            print(f"Checking: {r.get('guestName')} | Arrival: {arrival} @ {checkin_time} | Departure: {departure} @ {checkout_time}")

            if arrival < today < departure:
                print("Matched: in middle of stay.")
                selected = r
                break
            elif today == arrival and current_time >= checkin_time:
                print("Matched: just checked in.")
                selected = r
                break
            elif today == departure and current_time < checkout_time:
                print("Matched: last morning before checkout.")
                selected = r
                break
            else:
                print("No match for this reservation.")

        if selected:
            result = {
                "guestName": selected.get("guestName"),
                "checkIn": selected.get("arrivalDate"),
                "checkInTime": str(selected.get("checkInTime", 16)),
                "checkOut": selected.get("departureDate"),
                "checkOutTime": str(selected.get("checkOutTime", 10)),
                "numberOfGuests": str(selected.get("numberOfGuests")),
                "notes": selected.get("comment", "")
            }
            print("Selected reservation:", result)
            return jsonify(result), 200
        else:
            print("No guest currently checked in.")
            return jsonify({"message": "No guest currently checked in."}), 200

    except Exception as e:
        print("SERVER ERROR:", str(e))
        return jsonify({"error": "Internal server error", "details": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
