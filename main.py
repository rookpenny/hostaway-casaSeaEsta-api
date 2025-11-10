import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime
import requests
from dotenv import load_dotenv

from utils.hostaway import get_token, fetch_reservations

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

ALLOWED_LISTING_IDS = {"256853"}
LEGACY_PROPERTY_MAP = {"casa-sea-esta": "256853"}

# ---------- CATEGORY LOGIC ----------
def classify_category(message):
    message_lower = message.lower()

    if any(word in message_lower for word in ["urgent", "emergency", "fire", "leak", "locked out", "break", "flood"]):
        return "urgent"
    elif any(word in message_lower for word in ["repair", "broken", "not working", "malfunction", "maintenance"]):
        return "maintenance"
    elif any(word in message_lower for word in ["late checkout", "late check-out", "extend stay", "stay longer", "extra night", "another night", "add nights", "extend trip"]):
        return "extension"
    elif any(word in message_lower for word in ["can we", "is it possible", "could we", "extra", "more", "early checkin", "early check-in", "request"]):
        return "request"
    elif any(word in message_lower for word in ["tv", "wifi", "internet", "stream", "remote", "netflix", "entertainment"]):
        return "entertainment"
    else:
        return "other"

def smart_response(category):
    if category == "maintenance":
        return "Thanks for letting me know! I’ve passed this on to your host. They’ll respond shortly."
    elif category == "urgent":
        return "I’ve marked this as urgent and alerted your host right away.\n\n**If this is a real emergency**, please call them at +1-650-313-3724."
    elif category == "request":
        return "Got it! I’ve passed your request along. Let me know if there’s anything else I can help with in the meantime."
    elif category == "entertainment":
        return "Thanks for the heads-up! If you’re having trouble with the TV or internet, try restarting the modem and checking that the input source is correct.\n\nIf that doesn’t work, I’ve gone ahead and notified your host too."
    else:
        return "Thanks for your message! I’ve shared it with your host. They’ll follow up shortly."

# ---------- ROUTES ----------
@app.route("/")
def home():
    return jsonify({"message": "Welcome to Casa Sea Esta API!"}), 200

@app.route("/api/guest")
def get_guest_info():
    try:
        listing_id = request.args.get("listingId")
        if not listing_id:
            legacy_slug = request.args.get("property", "").lower().replace(" ", "-")
            listing_id = LEGACY_PROPERTY_MAP.get(legacy_slug)

        if listing_id not in ALLOWED_LISTING_IDS:
            return jsonify({"error": "Unknown or unauthorized listingId"}), 404

        token = get_token()
        reservations = fetch_reservations(listing_id, token)
        today = datetime.today().strftime("%Y-%m-%d")
        now = datetime.now()

        valid = []
        for r in reservations:
            check_in = r.get("arrivalDate")
            check_out = r.get("departureDate")
            check_in_time = int(r.get("checkInTime", 16))
            check_out_time = int(r.get("checkOutTime", 10))

            if r.get("status") in {"new", "modified", "confirmed", "accepted", "ownerStay"}:
                if check_in == today and now.hour >= check_in_time:
                    valid.append(r)
                elif check_in < today < check_out:
                    valid.append(r)
                elif check_out == today and now.hour < check_out_time:
                    valid.append(r)

        if not valid:
            return jsonify({"message": "No guest currently checked in."}), 404

        latest = max(valid, key=lambda r: r.get("updatedOn", ""))
        guest_info = {
            "guestName": latest.get("guestName"),
            "checkIn": latest.get("arrivalDate"),
            "checkInTime": str(latest.get("checkInTime", 16)),
            "checkOut": latest.get("departureDate"),
            "checkOutTime": str(latest.get("checkOutTime", 10)),
            "numberOfGuests": str(latest.get("numberOfGuests")),
            "phone": latest.get("phone"),
            "notes": latest.get("comment", "")
        }

        return jsonify(guest_info), 200

    except Exception as e:
        return jsonify({"error": "Internal server error", "details": str(e)}), 500

@app.route("/api/guest-authenticated")
def guest_authenticated():
    try:
        code = request.args.get("code")
        if not code or not code.isdigit():
            return jsonify({"error": "Invalid code format"}), 400

        listing_id = LEGACY_PROPERTY_MAP.get("casa-sea-esta")
        token = get_token()
        reservations = fetch_reservations(listing_id, token)
        today = datetime.today().strftime("%Y-%m-%d")
        now = datetime.now()

        for r in reservations:
            guest_name = r.get("guestName", "UNKNOWN")
            phone = r.get("phone", "")
            check_in = r.get("arrivalDate")
            check_out = r.get("departureDate")
            check_in_time = int(r.get("checkInTime", 16))
            check_out_time = int(r.get("checkOutTime", 10))
            status = r.get("status")

            is_current_guest = (
                (check_in == today and now.hour >= check_in_time) or
                (check_in < today < check_out) or
                (check_out == today and now.hour < check_out_time)
            )

            if status not in {"new", "modified", "confirmed", "accepted", "ownerStay"} or not is_current_guest:
                continue

            if not phone or len(phone) < len(code):
                continue

            if phone[-len(code):] == code:
                return jsonify({
                    "guestName": guest_name,
                    "phone": phone,
                    "property": "Casa Sea Esta",
                    "checkIn": check_in,
                    "checkOut": check_out
                }), 200

        return jsonify({"error": "Guest not found or not currently staying"}), 401

    except Exception as e:
        return jsonify({"error": "Internal server error", "details": str(e)}), 500

# ---------- NEXT AVAILABILITY ----------
@app.route('/api/next-availability', methods=['GET'])
def next_availability():
    property = request.args.get('property')
    if property != "Casa Sea Esta":
        return jsonify({"error": "Unknown property"}), 400

    try:
        listing_id = LEGACY_PROPERTY_MAP.get("casa-sea-esta")
        token = get_token()
        reservations = fetch_reservations(listing_id, token)

        today = datetime.utcnow().strftime("%Y-%m-%d")
        future_reservations = [r for r in reservations if r.get("arrivalDate") and r["arrivalDate"] > today]

        if future_reservations:
            next_booking = min(future_reservations, key=lambda r: r["arrivalDate"])
            next_start_date = next_booking["arrivalDate"]
        else:
            next_start_date = None

        delta = (datetime.strptime(next_start_date, '%Y-%m-%d').date() - datetime.utcnow().date()).days if next_start_date else "open-ended"

        return jsonify({
            "availableNights": max(0, delta) if isinstance(delta, int) else delta,
            "nextBookingStart": next_start_date
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------- GUEST MESSAGE SUBMISSION ----------
@app.route("/api/guest-message", methods=["POST"])
def save_guest_message():
    try:
        data = request.get_json()
        name = data.get("name")
        phone = data.get("phone") or data.get("phoneLast4")  # fallback
        message = data.get("message")
        date = data.get("date")
        category = classify_category(message)
        sandy_reply = smart_response(category)

        if not all([name, phone, message, date]):
            return jsonify({"error": "Missing fields"}), 400

        phone_last4 = phone[-4:]

        airtable_api_key = os.getenv("AIRTABLE_API_KEY")
        airtable_base_id = os.getenv("AIRTABLE_BASE_ID")
        table_id = "tblGEDhos73P2C5kn"
        airtable_url = f"https://api.airtable.com/v0/{airtable_base_id}/{table_id}"

        headers = {
            "Authorization": f"Bearer {airtable_api_key}",
            "Content-Type": "application/json"
        }

        fields = {
            "Name": name,
            "Phone": phone,  # full phone for emergencies
            "Phone Last 4": phone_last4,
            "Message": message,
            "Date": date,
            "Category": category,
            "Reply": sandy_reply
        }

        airtable_resp = requests.post(airtable_url, headers=headers, json={"fields": fields})

        if airtable_resp.status_code in [200, 201]:
            return jsonify({"success": True, "reply": sandy_reply}), 200
        else:
            return jsonify({"error": "Failed to save to Airtable", "details": airtable_resp.text}), 500

    except Exception as e:
        return jsonify({"error": "Unexpected server error", "details": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
