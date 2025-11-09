import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime
import requests
from dotenv import load_dotenv

from utils.hostaway import get_token, fetch_reservations
from utils.cloudinary_upload import upload_image_from_url  # ✅ Cloudinary integration

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

ALLOWED_LISTING_IDS = {"256853"}
LEGACY_PROPERTY_MAP = {"casa-sea-esta": "256853"}


@app.route("/debug-api-key")
def debug_api_key():
    key = os.getenv("OPENAI_API_KEY")
    if key:
        return jsonify({"message": "API key is set", "length": len(key)}), 200
    else:
        return jsonify({"error": "API key is missing"}), 500


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


@app.route("/api/guest-message", methods=["POST"])
def save_guest_message():
    try:
        data = request.get_json()
        name = data.get("name")
        phone_last4 = data.get("phoneLast4")
        message = data.get("message")
        category = data.get("category")
        attachment = data.get("attachment")
        date = data.get("date")

        if not all([name, phone_last4, message, date, category]):
            return jsonify({"error": "Missing fields"}), 400

        hosted_url = ""
        if attachment and "url" in attachment:
            openai_url = attachment["url"]
            filename = attachment.get("filename", "guest-upload.jpg")
            try:
                hosted_url = upload_image_from_url(openai_url, filename)
            except Exception as e:
                return jsonify({"error": "Cloudinary upload failed", "details": str(e)}), 500

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
            "Phone Last 4": phone_last4,
            "Message": message,
            "Date": date,
            "Category": category
        }

        if hosted_url:
            fields["Attachment"] = [{
                "url": hosted_url,
                "filename": filename
            }]

        payload = {"fields": fields}
        airtable_resp = requests.post(airtable_url, headers=headers, json=payload)

        if airtable_resp.status_code in [200, 201]:
            return jsonify({"success": True, "hostedImage": hosted_url}), 200
        else:
            return jsonify({"error": "Failed to save to Airtable", "details": airtable_resp.text}), 500

    except Exception as e:
        return jsonify({"error": "Unexpected server error", "details": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
