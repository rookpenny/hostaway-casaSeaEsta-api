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

# ✅ Accept Hostaway listing ID directly
ALLOWED_LISTING_IDS = {"256853"}

vibe_storage = {}

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
    return resp.json().get("access_token") if resp.ok else None

@app.route("/")
def home():
    return jsonify({"message": "Welcome to Casa Sea Esta API!"}), 200

@app.route("/api/guest")
def get_guest_info():
    try:
        listing_id = request.args.get("listingId")
        if listing_id not in ALLOWED_LISTING_IDS:
            return jsonify({"error": "Unknown or unauthorized listingId"}), 404

        token = get_token()
        if not token:
            return jsonify({"error": "Authentication failed"}), 401

        today = datetime.today().strftime("%Y-%m-%d")
        year = datetime.today().year
        month = datetime.today().month
        last_day = monthrange(year, month)[1]
        date_range_start = datetime.today().replace(day=1).strftime("%Y-%m-%d")
        date_range_end = datetime.today().replace(day=last_day).strftime("%Y-%m-%d")

        resp = requests.get(
            "https://api.hostaway.com/v1/reservations",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "listingId": listing_id,
                "dateFrom": date_range_start,
                "dateTo": date_range_end
            }
        )
        data = resp.json()
        reservations = data.get("result", [])

        valid = []
        for r in reservations:
            check_in = r.get("arrivalDate")
            check_out = r.get("departureDate")
            check_in_time = int(r.get("checkInTime", 16))
            check_out_time = int(r.get("checkOutTime", 10))
            now = datetime.now()

            if r.get("status") in {"new", "modified", "confirmed", "accepted"}:
                if check_in == today and now.hour >= check_in_time:
                    valid.append(r)
                elif check_in < today < check_out:
                    valid.append(r)
                elif check_out == today and now.hour < check_out_time:
                    valid.append(r)

        if not valid:
            return jsonify({"message": "No guest currently checked in."}), 404

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

        return jsonify(selected), 200

    except Exception as e:
        return jsonify({"error": "Internal server error", "details": str(e)}), 500

@app.route("/api/vibe-message", methods=["POST"])
def save_vibe_message():
    try:
        data = request.json
        vibe_storage["message"] = data.get("message")
        vibe_storage["guestName"] = data.get("guestName")
        vibe_storage["timestamp"] = datetime.now().isoformat()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/vibe-message", methods=["GET"])
def get_vibe_message():
    if "message" in vibe_storage:
        return jsonify(vibe_storage), 200
    return jsonify({"message": "No vibe message set"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
