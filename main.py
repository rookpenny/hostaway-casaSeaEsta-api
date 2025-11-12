import os
import time
import requests

from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timedelta
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

def safe_fetch_reservations(listing_id, retries=3, delay=1):
    for attempt in range(retries):
        try:
            return fetch_reservations(listing_id, get_token())
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(delay * (2 ** attempt))  # Exponential backoff
    raise Exception("Failed to fetch reservations after retries.")

def find_upcoming_guest_by_code(code: str):
    """Search upcoming real reservations using the last 4 digits of the guest's phone number."""
    try:
        listing_id = LEGACY_PROPERTY_MAP["casa-sea-esta"]
        token = get_token()
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

            if 0 <= days_until_checkin <= 3:
                return {
                    "name": r.get("guestName", "Guest"),
                    "phone": phone,
                    "property": "Casa Sea Esta",
                    "checkin_date": checkin_str,
                    "checkout_date": r.get("departureDate")
                }

    except Exception as e:
        print(f"Error in find_upcoming_guest_by_code: {e}")
        return None

# ---------- ROUTES ----------
@app.route("/")
def home():
    return jsonify({"message": "Welcome to Casa Sea Esta API!"}), 200

@app.route("/docs/openapi.yaml")
def serve_openapi():
    return app.send_static_file("docs/openapi.yaml")

@app.route("/api/debug/upcoming-guests")
def debug_upcoming_guests():
    try:
        property_name = request.args.get("property", "").lower().replace(" ", "-")
        days_out = int(request.args.get("days_out", 20))

        listing_id = LEGACY_PROPERTY_MAP.get(property_name)
        if not listing_id:
            return jsonify({"error": "Unknown property"}), 400

        token = get_token()
        reservations = fetch_reservations(listing_id, token)

        today = datetime.utcnow().date()
        end_date = today + timedelta(days=days_out)

        guests = []
        for r in reservations:
            try:
                checkin = datetime.strptime(r.get("arrivalDate", ""), "%Y-%m-%d").date()
                if today <= checkin <= end_date:
                    guests.append({
                        "name": r.get("guestName", "Unknown"),
                        "phone": r.get("phone", "N/A"),
                        "arrivalDate": r.get("arrivalDate"),
                        "departureDate": r.get("departureDate"),
                        "status": r.get("status", "unknown")
                    })
            except Exception as inner_e:
                print(f"Error parsing reservation: {inner_e}")

        return jsonify({"results": guests, "count": len(guests)})

    except Exception as e:
        return jsonify({"error": "Unexpected error", "details": str(e)}), 500

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

        # STEP 1: Try to match a current guest (same as before)
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
                                   "Need local recs, help with the house, or want to extend your stay? I’ve got you covered! ☀️",
                        "verified": True
                    })

        # STEP 2: No current guest — try future guest for readiness help
        guest = find_upcoming_guest_by_code(code)
        if guest:
            return jsonify({
                "guestName": guest["name"],
                "phone": guest["phone"],
                "property": guest["property"],
                "checkIn": guest["checkin_date"],
                "checkOut": guest["checkout_date"],
                "message": f"Hey hey! It looks like your stay hasn’t kicked off just yet, so I can’t verify you until check-in day. 🕓\n"
                           "BUT — I’d love to help you get ready! Want to know:\n"
                           "- ✅ What to expect on arrival\n"
                           "- 🏡 How check-in works\n"
                           "- 📍 Where to find stuff like Wi-Fi or towels?\n"
                           "Just say the word!",
                "prearrival": True
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

@app.route("/api/guest-message", methods=["POST"])
def save_guest_message():
    try:
        import urllib.parse
        data = request.get_json()

        # ✅ Required fields (simplified — no more phoneLast4)
        required_fields = ["name", "phone", "date", "category"]
        has_message = "message" in data and data["message"]

        if not all(field in data and data[field] for field in required_fields) or not has_message:
            return jsonify({"error": "Missing required fields"}), 400

        reply = smart_response(data["category"])

        # Airtable setup
        airtable_url = f"https://api.airtable.com/v0/{os.getenv('AIRTABLE_BASE_ID')}/tblGEDhos73P2C5kn"
        headers = {
            "Authorization": f"Bearer {os.getenv('AIRTABLE_API_KEY')}",
            "Content-Type": "application/json"
        }

        # Build the record
        airtable_data = {
            "fields": {
                "Name": data["name"],
                "Full Phone": data["phone"],
                "Date": data["date"],
                "Category": data["category"],
                "Message": data["message"],
                "Reply": reply
            }
        }

        # Send to Airtable
        response = requests.post(airtable_url, headers=headers, json=airtable_data)

        if response.status_code in [200, 201]:
            return jsonify({"success": True, "reply": reply}), 200
        else:
            return jsonify({"error": "Failed to save to Airtable", "details": response.text}), 500

    except Exception as e:
        return jsonify({"error": "Unexpected server error", "details": str(e)}), 500


import os
import requests
from flask import jsonify, request

@app.route("/api/prearrival-options")
def prearrival_options():
    try:
        # ✅ Require phone param (even if unused — for API consistency)
        phone = request.args.get("phone")
        if not phone:
            return jsonify({"error": "Phone number is required"}), 400

        # ✅ Airtable config
        AIRTABLE_TOKEN = os.getenv("AIRTABLE_PREARRIVAL_API_KEY")
        BASE_ID = os.getenv("AIRTABLE_PREARRIVAL_BASE_ID")
        TABLE_ID = "tblviNlbgLbdEalOj"  # Hardcoded table ID

        # ✅ Build request
        url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
        headers = {
            "Authorization": f"Bearer {AIRTABLE_TOKEN}"
        }

        # ✅ Fetch from Airtable
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return jsonify({"error": "Failed to fetch from Airtable", "details": response.text}), 500

        records = response.json().get("records", [])
        options = []

        # ✅ Only include options where 'active' is checked
        for record in records:
            fields = record.get("fields", {})
            if not fields.get("active"):  # <- filter only active
                continue

            options.append({
                "id": fields.get("id"),
                "label": fields.get("label"),
                "description": fields.get("description"),
                "price": fields.get("price")
            })

        return jsonify({"options": options}), 200

    except Exception as e:
        return jsonify({"error": "Unexpected error", "details": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
