import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime
import requests
from dotenv import load_dotenv

from utils.hostaway import get_token, fetch_reservations

# Load .env variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

# Constants
ALLOWED_LISTING_IDS = {"256853"}
LEGACY_PROPERTY_MAP = {"casa-sea-esta": "256853"}
EMERGENCY_PHONE = "+1-650-313-3724"

# ---------- CLASSIFICATION ----------
def classify_category(message: str) -> str:
    message_lower = message.lower()

    if any(term in message_lower for term in ["urgent", "emergency", "fire", "leak", "locked out", "break", "flood"]):
        return "urgent"
    elif any(term in message_lower for term in ["repair", "broken", "not working", "malfunction", "maintenance"]):
        return "maintenance"
    elif any(term in message_lower for term in ["late checkout", "extend stay", "stay longer", "extra night", "add nights", "extend trip"]):
        return "extension"
    elif any(term in message_lower for term in ["can we", "is it possible", "request", "early check-in", "extra"]):
        return "request"
    elif any(term in message_lower for term in ["tv", "wifi", "internet", "remote", "stream", "netflix"]):
        return "entertainment"
    return "other"

def smart_response(category: str) -> str:
    responses = {
        "urgent": f"I’ve marked this as urgent and alerted your host right away.\n\n**If this is a real emergency**, please call them at {EMERGENCY_PHONE}.",
        "maintenance": "Thanks for letting me know! I’ve passed this on to your host. They’ll respond shortly.",
        "request": "Got it! I’ve passed your request along. Let me know if there’s anything else I can help with in the meantime.",
        "entertainment": "Thanks for the heads-up! Try restarting the modem and checking the input source. I’ve notified your host too.",
        "other": "Thanks for your message! I’ve shared it with your host. They’ll follow up shortly."
    }
    return responses.get(category, responses["other"])

# ---------- UTILS ----------
def calculate_extra_nights(next_start_date: str) -> int | str:
    if not next_start_date:
        return "open-ended"
    today = datetime.utcnow().date()
    next_date = datetime.strptime(next_start_date, '%Y-%m-%d').date()
    return max(0, (next_date - today).days)

# ---------- ROUTES ----------
@app.route("/")
def home():
    return jsonify({"message": "Welcome to Casa Sea Esta API!"}), 200

@app.route("/api/guest")
def get_guest_info():
    try:
        listing_id = request.args.get("listingId") or LEGACY_PROPERTY_MAP.get(request.args.get("property", "").lower().replace(" ", "-"))
        if listing_id not in ALLOWED_LISTING_IDS:
            return jsonify({"error": "Unknown or unauthorized listingId"}), 404

        token = get_token()
        reservations = fetch_reservations(listing_id, token)

        today = datetime.today().strftime("%Y-%m-%d")
        now = datetime.now()
        valid_reservations = []

        for r in reservations:
            if r.get("status") not in {"new", "modified", "confirmed", "accepted", "ownerStay"}:
                continue
            check_in, check_out = r.get("arrivalDate"), r.get("departureDate")
            if not check_in or not check_out:
                continue

            check_in_time = int(r.get("checkInTime", 16))
            check_out_time = int(r.get("checkOutTime", 10))

            if (
                (check_in == today and now.hour >= check_in_time) or
                (check_in < today < check_out) or
                (check_out == today and now.hour < check_out_time)
            ):
                valid_reservations.append(r)

        if not valid_reservations:
            return jsonify({"message": "No guest currently checked in."}), 404

        latest = max(valid_reservations, key=lambda r: r.get("updatedOn", ""))
        return jsonify({
            "guestName": latest.get("guestName"),
            "checkIn": latest.get("arrivalDate"),
            "checkInTime": str(latest.get("checkInTime", 16)),
            "checkOut": latest.get("departureDate"),
            "checkOutTime": str(latest.get("checkOutTime", 10)),
            "numberOfGuests": str(latest.get("numberOfGuests")),
            "phone": latest.get("phone"),
            "notes": latest.get("comment", "")
        })

    except Exception as e:
        return jsonify({"error": "Internal server error", "details": str(e)}), 500

@app.route("/api/guest-authenticated")
def guest_authenticated():
    try:
        code = request.args.get("code")
        if not code or not code.isdigit():
            return jsonify({"error": "Invalid code format"}), 400

        listing_id = LEGACY_PROPERTY_MAP["casa-sea-esta"]
        token = get_token()
        reservations = fetch_reservations(listing_id, token)

        today = datetime.today().strftime("%Y-%m-%d")
        now = datetime.now()

        for r in reservations:
            phone = r.get("phone", "")
            if not phone or len(phone) < len(code):
                continue

            if phone.endswith(code):
                guest_name = r.get("guestName", "there")
                check_in, check_out = r.get("arrivalDate"), r.get("departureDate")
                check_in_time = int(r.get("checkInTime", 16))
                check_out_time = int(r.get("checkOutTime", 10))
                status = r.get("status")

                is_current_guest = (
                    (check_in == today and now.hour >= check_in_time) or
                    (check_in < today < check_out) or
                    (check_out == today and now.hour < check_out_time)
                )

                if status in {"new", "modified", "confirmed", "accepted", "ownerStay"} and is_current_guest:
                    return jsonify({
                        "guestName": guest_name,
                        "phone": phone,
                        "property": "Casa Sea Esta",
                        "checkIn": check_in,
                        "checkOut": check_out,
                        "message": f"You're all set, {guest_name} — welcome to Casa Sea Esta! 🌴\n"
                                   "Need local recs, help with the house, or want to extend your stay? I’ve got you covered! ☀️"
                    })

        return jsonify({"error": "Guest not found or not currently staying"}), 401

    except Exception as e:
        return jsonify({"error": "Internal server error", "details": str(e)}), 500

@app.route("/api/next-availability")
def next_availability():
    if request.args.get('property') != "Casa Sea Esta":
        return jsonify({"error": "Unknown property"}), 400

    try:
        listing_id = LEGACY_PROPERTY_MAP["casa-sea-esta"]
        token = get_token()
        reservations = fetch_reservations(listing_id, token)

        today = datetime.utcnow().strftime("%Y-%m-%d")
        future = [r for r in reservations if r.get("arrivalDate") and r["arrivalDate"] > today]
        next_start = min(future, key=lambda r: r["arrivalDate"])["arrivalDate"] if future else None
        nights = calculate_extra_nights(next_start)

        return jsonify({"availableNights": nights, "nextBookingStart": next_start})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
