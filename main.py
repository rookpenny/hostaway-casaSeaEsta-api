import os
from flask import Flask, jsonify, request, Response
from flask_cors import CORS
from datetime import datetime
import requests

from utils.hostaway import get_token, fetch_reservations

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

ALLOWED_LISTING_IDS = {"256853"}
LEGACY_PROPERTY_MAP = { "casa-sea-esta": "256853" }
vibe_storage = {}

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

        # Upload image to your website folder if available
        hosted_url = ""
        if attachment and "url" in attachment:
            openai_url = attachment["url"]
            filename = attachment.get("filename", "guest-upload.jpg")

            # 🛡️ Fetch image and validate content type
            img_resp = requests.get(openai_url)
            content_type = img_resp.headers.get("Content-Type", "")

            if "image" not in content_type:
                return jsonify({"error": "The provided URL did not return an image."}), 400

            img_data = img_resp.content

            # 📤 Upload to your hosting folder
            upload_url = "https://wordpress-1513490-5816047.cloudwaysapps.com/Hostscout/Casa-Sea-Esta/upload.php"
            files = {'file': (filename, img_data)}
            upload_resp = requests.post(upload_url, files=files)

            if upload_resp.status_code == 200:
                upload_json = upload_resp.json()
                hosted_url = upload_json.get("url")
            else:
                return jsonify({"error": "Failed to upload image to hosting server"}), 500

        # 📝 Prepare Airtable payload
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

        payload = { "fields": fields }
        response = requests.post(airtable_url, headers=headers, json=payload)

        # ✅ Return HTML confirmation
        if response.status_code in [200, 201]:
            html = f"""
                <html>
                    <body>
                        <h2>✅ Message Saved to Airtable</h2>
                        <p><strong>Name:</strong> {name}</p>
                        <p><strong>Phone:</strong> {phone_last4}</p>
                        <p><strong>Message:</strong> {message}</p>
                        <p><strong>Category:</strong> {category}</p>
                        <p><strong>Date:</strong> {date}</p>
                        <p><strong>Image:</strong> {hosted_url or 'None'}</p>
                        {'<img src="' + hosted_url + '" width="300"/>' if hosted_url else ''}
                    </body>
                </html>
            """
            return Response(html, mimetype="text/html")

        return jsonify({
            "error": "Failed to save to Airtable",
            "details": response.text
        }), 500

    except Exception as e:
        return jsonify({"error": "Unexpected server error", "details": str(e)}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
