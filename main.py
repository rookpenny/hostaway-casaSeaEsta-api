import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime
from dotenv import load_dotenv

from utils.hostaway import get_token, fetch_reservations

load_dotenv()
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

# ✅ Allowed listing IDs (Hostaway PMS IDs)
ALLOWED_LISTING_IDS = {"256853"}

# 🔁 Optional: Legacy mapping from slug to ID
LEGACY_PROPERTY_MAP = {
    "casa-sea-esta": "256853"
}

# 🧠 In-memory store for vibe message
vibe_storage = {}

@app.route("/")
def home():
    return jsonify({"message": "Welcome to Casa Sea Esta API!"}), 200

@app.route("/api/guest")
def get_guest_info():
    try:
        # ✅ Accept listingId or fallback to legacy ?property=
        listing_id = request.args.get("listingId")
        if not listing_id:
            legacy_slug = request.args.get("property")
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

            if r.get("status") in {"new", "modified", "confirmed", "accepted"}:
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
            "notes": latest.get("comment", "")
        }

        return jsonify(guest_info), 200

    except Exception as e:
        return jsonify({"error": "Internal server error", "details": str(e)}), 500

@app.route("/api/guest-authenticated")
def guest_authenticated():
    try:
        code = request.args.get("code")

        if not code or not code.isdigit() or len(code) != 4:
            return jsonify({"error": "Invalid code format"}), 400

        listing_id = LEGACY_PROPERTY_MAP.get("casa-sea-esta")
        token = get_token()
        reservations = fetch_reservations(listing_id, token)
        today = datetime.today().strftime("%Y-%m-%d")
        now = datetime.now()

        print("\n===== DEBUG START =====")
        print(f"Incoming code: {code}")
        print(f"Today's date: {today}, Hour: {now.hour}")
        print("========================")

        for r in reservations:
            phone = r.get("contactPhone1", "")
            guest_name = r.get("guestName", "")
            check_in = r.get("arrivalDate")
            check_out = r.get("departureDate")
            check_in_time = int(r.get("checkInTime", 16))
            check_out_time = int(r.get("checkOutTime", 10))
            status = r.get("status")

            print(f"\n--- Checking {guest_name} ---")
            print(f"Phone: {phone} | Ends with code? {phone.endswith(code)}")
            print(f"Check-in: {check_in} @ {check_in_time}:00")
            print(f"Check-out: {check_out} @ {check_out_time}:00")
            print(f"Status: {status}")

            is_current_guest = (
                (check_in == today and now.hour >= check_in_time) or
                (check_in < today < check_out) or
                (check_out == today and now.hour < check_out_time)
            )

            print(f"is_current_guest? {is_current_guest}")

            if status in {"new", "modified", "confirmed", "accepted"} and is_current_guest and phone.endswith(code):
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

@app.route("/api/vibe-message", methods=["GET"])
def get_vibe_message():
    if "message" in vibe_storage:
        return jsonify(vibe_storage), 200
    return jsonify({"message": "No vibe message set"}), 404

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
