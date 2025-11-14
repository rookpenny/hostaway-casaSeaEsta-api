import os
import json
import time
import requests
import logging

from datetime import datetime, timedelta
from functools import lru_cache
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from dotenv import load_dotenv

from utils.hostaway import get_token, fetch_reservations
from utils.config import load_property_config  # Ensure this loads per-property configs

#config = load_property_config(slug)
#emergency_phone = config.get("emergency_phone", "N/A")


# ----------- CONFIG LOADER -----------
def load_property_config(slug: str) -> dict:
    path = f"data/{slug}/config.json"
    if not os.path.exists(path):
        raise FileNotFoundError(f"No config found for {slug}")
    with open(path) as f:
        return json.load(f)

# ----------- TOKEN CACHING -----------
@lru_cache(maxsize=1)
def cached_token():
    return get_token()

# ----------- FLASK INIT -----------
load_dotenv()
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

# ----------- CONSTANTS -----------
#ALLOWED_LISTING_IDS = {"256853"}  # Expand this if adding more properties
#LEGACY_PROPERTY_MAP = {"casa-sea-esta": "256853"}  # Consider removing this when all configs move to file-based
#EMERGENCY_PHONE = "+1-650-313-3724"  # Consider moving this to per-property config

# ----------- MESSAGE CLASSIFICATION -----------
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

def smart_response(category: str, emergency_phone: str) -> str:
    responses = {
        "urgent": f"I’ve marked this as urgent and alerted your host right away.\n\n**If this is a real emergency**, please call them at {emergency_phone}.",
        "maintenance": "Thanks for letting me know! I’ve passed this on to your host. They’ll respond shortly.",
        "request": "Got it! I’ve passed your request along. Let me know if there’s anything else I can help with in the meantime.",
        "entertainment": "Thanks for the heads-up! Try restarting the modem and checking the input source. I’ve notified your host too.",
        "other": "Thanks for your message! I’ve shared it with your host. They’ll follow up shortly."
    }
    return responses.get(category, responses["other"])

# ----------- LOG TYPE MAPPING -----------
def map_log_type(message: str) -> str:
    message_lower = message.lower()

    if any(term in message_lower for term in ["early check-in", "early checkin", "early access", "early arrival"]):
        return "Early Access Request"
    elif any(term in message_lower for term in ["fridge stocking", "stock the fridge", "grocery", "groceries", "pre-stock"]):
        return "Fridge Stocking Request"
    elif any(term in message_lower for term in ["extend", "late checkout", "extra night", "add night", "stay longer"]):
        return "Extension Request"
    elif "refer" in message_lower:
        return "Referral"
    elif "email" in message_lower and any(term in message_lower for term in ["list", "opt", "stay connected"]):
        return "Email Opt-In"
    elif any(term in message_lower for term in ["maintenance", "broken", "repair", "not working"]):
        return "Maintenance"
    elif any(term in message_lower for term in ["urgent", "emergency", "flood", "leak", "locked out", "fire"]):
        return "Urgent Issue"

    return "Guest Message"


# ---------- UTILS ----------

def calculate_extra_nights(next_start_date: str) -> int | str:
    if not next_start_date:
        return "open-ended"
    today = datetime.utcnow().date()
    next_date = datetime.strptime(next_start_date, '%Y-%m-%d').date()
    return max(0, (next_date - today).days)

def safe_fetch_reservations(listing_id: str, retries: int = 3, delay: int = 1) -> list:
    for attempt in range(retries):
        try:
            return fetch_reservations(listing_id, cached_token())
        except Exception as e:
            logging.warning(f"[Reservations] Attempt {attempt + 1} failed: {e}")
            time.sleep(delay * (2 ** attempt))  # Exponential backoff
    raise Exception("Failed to fetch reservations after retries.")

def find_upcoming_guest_by_code(code: str, slug: str) -> dict | None:
    try:
        config = load_property_config(slug)
        listing_id = config["listing_id"]
        property_name = config.get("property_name", slug.replace("-", " ").title())

        token = cached_token()
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

            if 0 <= days_until_checkin <= 20:
                return {
                    "name": r.get("guestName", "Guest"),
                    "phone": phone,
                    "property": property_name,
                    "checkin_date": checkin_str,
                    "checkout_date": r.get("departureDate")
                }

    except Exception as e:
        logging.error(f"[Guest Lookup] Error in find_upcoming_guest_by_code: {e}")
        return None


# ---------- LOG TYPE DETECTION ----------

LOG_TYPE_MAP = {
    "early check": ["Early Access Request"],
    "early access": ["Early Access Request"],
    "fridge": ["Fridge Stocking Request"],
    "stock": ["Fridge Stocking Request"],
    "groceries": ["Fridge Stocking Request"],
    "extend": ["Extension Request"],
    "longer": ["Extension Request"],
    "refer": ["Referral"],
    "email": ["Email Opt-In"],
    "maintenance": ["Maintenance"],
    "urgent": ["Urgent Issue"]
}

def detect_log_types(message: str) -> list[str]:
    message_lower = message.lower()
    return list({
        log_type
        for keyword, types in LOG_TYPE_MAP.items()
        if keyword in message_lower
        for log_type in types
    }) or ["Guest Message"]

# ---------- LOG TYPE DETECTION ----------

@app.route("/")
def home():
    return jsonify({"message": "Welcome to the multi-property Sandy API!"}), 200


@app.route("/health")
def health_check():
    return jsonify({"status": "ok"}), 200


@app.route("/docs/openapi.yaml")
def serve_openapi():
    return app.send_static_file("docs/openapi.yaml")


@app.route("/debug")
def serve_debug_ui():
    return render_template("debug.html")

@app.route("/api/debug/property-config")
def debug_property_config():
    slug = request.args.get("property", "casa-sea-esta").lower().replace(" ", "-")
    try:
        config = load_property_config(slug)
        return jsonify(config)
    except Exception as e:
        return jsonify({"error": str(e)}), 404


@app.route("/some-endpoint")
def some_endpoint():
    slug = request.args.get("property", "casa-sea-esta").lower().replace(" ", "-")  # Fallback if not provided
    try:
        config = load_property_config(slug)
    except FileNotFoundError:
        return jsonify({"error": f"Missing config for: {slug}"}), 404
    except Exception as e:
        return jsonify({"error": f"Config load error: {str(e)}"}), 500

    # ✅ Now use config safely
    listing_id = config.get("listing_id")
    emergency_phone = config.get("emergency_phone", "N/A")

    return jsonify({
        "property": config.get("property"),
        "listing_id": listing_id,
        "emergency_phone": emergency_phone
    })


        
@app.route("/admin/config/<slug>", methods=["GET"])
def get_config(slug):
    try:
        config = load_property_config(slug)
        return jsonify(config)
    except FileNotFoundError:
        return jsonify({"error": "Config not found"}), 404

#@app.route("/admin/config/<slug>", methods=["POST"])
#def save_config(slug):
#    try:
#        data = request.get_json()
#        config_path = f"data/{slug}/config.json"
#        os.makedirs(os.path.dirname(config_path), exist_ok=True)
#        with open(config_path, "w") as f:
#            json.dump(data, f, indent=2)
#        return jsonify({"success": True})
#    except Exception as e:
#        return jsonify({"error": str(e)}), 500


ALLOWED_STATUSES = {"new", "modified", "confirmed", "accepted", "ownerStay"}


@app.route("/api/refer", methods=["POST"])
def refer_friend():
    try:
        data = request.get_json()
        name = data.get("name")
        phone = data.get("phone")

        if not name or not phone:
            return jsonify({"error": "Missing required fields"}), 400

        airtable_url = f"https://api.airtable.com/v0/{os.getenv('AIRTABLE_BASE_ID')}/tblGEDhos73P2C5kn"
        headers = {
            "Authorization": f"Bearer {os.getenv('AIRTABLE_API_KEY')}",
            "Content-Type": "application/json"
        }

        payload = {
            "fields": {
                "Name": name,
                "Full Phone": phone,
                "Date": datetime.utcnow().strftime("%Y-%m-%d"),
                "Category": "referral",
                "Message": "Guest requested a referral link.",
                "Reply": "Referral link sent.",
                "Log Type": "Referral"
            }
        }

        response = requests.post(airtable_url, headers=headers, json=payload)
        if response.status_code not in [200, 201]:
            return jsonify({"error": "Failed to save referral log", "details": response.text}), 500

        referral_link = f"https://casaseaesta.com/referral?from={phone[-4:]}"
        return jsonify({
            "success": True,
            "message": "Here’s your referral link!",
            "link": referral_link
        })

    except Exception as e:
        return jsonify({"error": "Unexpected error", "details": str(e)}), 500


@app.route("/api/join-email", methods=["POST"])
def join_email_list():
    try:
        data = request.get_json()
        name = data.get("name")
        phone = data.get("phone")
        email = data.get("email")

        if not all([name, phone, email]):
            return jsonify({"error": "Missing name, phone, or email"}), 400

        airtable_url = f"https://api.airtable.com/v0/{os.getenv('AIRTABLE_BASE_ID')}/tblGEDhos73P2C5kn"
        headers = {
            "Authorization": f"Bearer {os.getenv('AIRTABLE_API_KEY')}",
            "Content-Type": "application/json"
        }

        payload = {
            "fields": {
                "Name": name,
                "Full Phone": phone,
                "Date": datetime.utcnow().strftime("%Y-%m-%d"),
                "Category": "email_opt_in",
                "Message": f"{name} ({email}) opted into the email list.",
                "Reply": "N/A",
                "Log Type": "Email List Opt-in"
            }
        }

        response = requests.post(airtable_url, headers=headers, json=payload)
        if response.status_code not in [200, 201]:
            return jsonify({"error": "Failed to log email opt-in", "details": response.text}), 500

        return jsonify({"success": True, "message": "You're on the list — welcome!"})

    except Exception as e:
        return jsonify({"error": "Unexpected error", "details": str(e)}), 500


@app.route("/api/debug/upcoming-guests")
def debug_upcoming_guests():
    # 🔐 API key check
    api_key = request.headers.get("X-API-KEY")
    expected_key = os.getenv("ADMIN_API_KEY")
    if api_key != expected_key:
        return jsonify({"error": "Unauthorized"}), 403

    try:
        slug = request.args.get("property", "casa-sea-esta").lower().replace(" ", "-")
        days_out = int(request.args.get("days_out", 20))

        # ✅ Load config dynamically
        try:
            config = load_property_config(slug)
        except FileNotFoundError:
            return jsonify({"error": f"No config found for '{slug}'"}), 404

        listing_id = config.get("listing_id")
        if not listing_id:
            return jsonify({"error": "Missing listing_id in config"}), 400

        token = cached_token()
        reservations = fetch_reservations(listing_id, token)

        today = datetime.utcnow().date()
        end_date = today + timedelta(days=days_out)

        guests = []
        for r in reservations:
            try:
                checkin = datetime.strptime(r.get("arrivalDate", ""), "%Y-%m-%d").date()
                status = r.get("status", "").lower()

                if status not in ALLOWED_STATUSES:
                    continue

                if today <= checkin <= end_date:
                    guests.append({
                        "name": r.get("guestName", "Unknown"),
                        "phone": r.get("phone", "N/A"),
                        "arrivalDate": r.get("arrivalDate"),
                        "departureDate": r.get("departureDate"),
                        "status": r.get("status", "unknown")
                    })
            except Exception as inner_e:
                logging.warning(f"[Upcoming Guests] Reservation parse error: {inner_e}")

        return jsonify({"results": guests, "count": len(guests)})

    except Exception as e:
        return jsonify({"error": "Unexpected error", "details": str(e)}), 500


@app.route("/api/guest")
def get_guest_info():
    slug = request.args.get("property", "casa-sea-esta").lower().replace(" ", "-")  # Default fallback

    try:
        config = load_property_config(slug)
    except FileNotFoundError:
        return jsonify({"error": f"No config found for '{slug}'"}), 404
    except Exception as e:
        return jsonify({"error": f"Failed to load config: {str(e)}"}), 500

    try:
        listing_id = config.get("listing_id")
        if not listing_id:
            return jsonify({"error": "Missing listing_id in config"}), 400

        token = cached_token()
        reservations = fetch_reservations(listing_id, token)

        today = datetime.today().strftime("%Y-%m-%d")
        now = datetime.now()
        valid_reservations = []

        for r in reservations:
            if r.get("status") not in ALLOWED_STATUSES:
                continue
            check_in, check_out = r.get("arrivalDate"), r.get("departureDate")
            if not check_in or not check_out:
                continue

            check_in_time = int(r.get("checkInTime", config.get("default_checkin_time", 16)))
            check_out_time = int(r.get("checkOutTime", config.get("default_checkout_time", 10)))

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
            "checkInTime": str(latest.get("checkInTime", config.get("default_checkin_time", 16))),
            "checkOut": latest.get("departureDate"),
            "checkOutTime": str(latest.get("checkOutTime", config.get("default_checkout_time", 10))),
            "numberOfGuests": str(latest.get("numberOfGuests")),
            "phone": latest.get("phone"),
            "notes": latest.get("comment", "")
        })

    except Exception as e:
        return jsonify({"error": "Unexpected server error", "details": str(e)}), 500


@app.route("/api/guest-authenticated")
def guest_authenticated():
    try:
        code = request.args.get("code")
        slug = request.args.get("property", "casa-sea-esta").lower().replace(" ", "-")

        if not code or not code.isdigit():
            return jsonify({"error": "Invalid code format"}), 400

        try:
            config = load_property_config(slug)
        except FileNotFoundError:
            return jsonify({"error": f"No config found for '{slug}'"}), 404

        listing_id = config.get("listing_id")
        property_name = config.get("property", slug.replace("-", " ").title())
        emergency_phone = config.get("emergency_phone", "N/A")

        if not listing_id:
            return jsonify({"error": "Missing listing_id in config"}), 400

        token = cached_token()
        reservations = fetch_reservations(listing_id, token)

        today = datetime.today().strftime("%Y-%m-%d")
        now = datetime.now()

        # STEP 1: Match current guest
        for r in reservations:
            phone = r.get("phone", "")
            if not phone or not phone.endswith(code):
                continue

            guest_name = r.get("guestName", "there")
            check_in = r.get("arrivalDate")
            check_out = r.get("departureDate")
            check_in_time = int(r.get("checkInTime", config.get("default_checkin_time", 16)))
            check_out_time = int(r.get("checkOutTime", config.get("default_checkout_time", 10)))
            status = r.get("status")

            is_current_guest = (
                (check_in == today and now.hour >= check_in_time) or
                (check_in < today < check_out) or
                (check_out == today and now.hour < check_out_time)
            )

            if status in ALLOWED_STATUSES and is_current_guest:
                return jsonify({
                    "guestName": guest_name,
                    "phone": phone,
                    "property": property_name,
                    "checkIn": check_in,
                    "checkOut": check_out,
                    "message": f"You're all set, {guest_name} — welcome to {property_name}! 🌴\n"
                               "Need local recs, help with the house, or want to extend your stay? I’ve got you covered! ☀️",
                    "verified": True
                })

        # STEP 2: Future guest — readiness support
        guest = find_upcoming_guest_by_code(code, slug)
        if guest:
            try:
                airtable_url = f"https://api.airtable.com/v0/{os.getenv('AIRTABLE_BASE_ID')}/tblGEDhos73P2C5kn"
                headers = {
                    "Authorization": f"Bearer {os.getenv('AIRTABLE_API_KEY')}",
                    "Content-Type": "application/json"
                }

                log_data = {
                    "fields": {
                        "Name": guest["name"],
                        "Full Phone": guest["phone"],
                        "Date": datetime.utcnow().strftime("%Y-%m-%d"),
                        "Category": "prearrival",
                        "Message": "Guest was verified early (prearrival).",
                        "Reply": "N/A",
                        "Log Type": "Prearrival Verification"
                    }
                }

                requests.post(airtable_url, headers=headers, json=log_data)

            except Exception as airtable_log_error:
                logging.warning(f"[Airtable] Logging error: {airtable_log_error}")

            return jsonify({
                "guestName": guest["name"],
                "phone": guest["phone"],
                "property": guest["property"],
                "checkIn": guest["checkin_date"],
                "checkOut": guest["checkout_date"],
                "message": f"Hey hey! It looks like your stay hasn’t kicked off just yet, so I can’t verify you until check-in day @ 4pm. 🕓\n"
                           "BUT — I’d love to help you get ready! Want to know:\n"
                           "- ✅ What to expect on arrival\n"
                           "- 🏡 How check-in works\n"
                           "- 📍Want early access or a pre-stocked fridge?\n"
                           "Just say the word!",
                "prearrival": True
            })

        return jsonify({"error": "Guest not found or not currently staying"}), 401

    except Exception as e:
        return jsonify({"error": "Internal server error", "details": str(e)}), 500

@app.route("/api/guest-message", methods=["POST"])
def save_guest_message():
    try:
        slug = request.args.get("property", "casa-sea-esta").lower().replace(" ", "-")
        config = load_property_config(slug)
        emergency_phone = config.get("emergency_phone", "N/A")

        data = request.get_json()

        # ✅ Required fields
        required_fields = ["name", "phone", "date", "message"]
        if not all(field in data and data[field] for field in required_fields):
            return jsonify({"error": "Missing required fields"}), 400

        name = data["name"]
        phone = data["phone"]
        date = data["date"]
        message = data["message"]

        # 🔍 Detect early access or fridge interest
        def matches_early_access_or_fridge(msg: str) -> bool:
            triggers = [
                "early access", "early check-in", "early checkin", "early arrival",
                "fridge stocking", "stock the fridge", "grocery drop",
                "fridge pre-stock", "can you stock", "groceries before arrival"
            ]
            return any(trigger in msg.lower() for trigger in triggers)

        if matches_early_access_or_fridge(message):
            try:
                AIRTABLE_TOKEN = os.getenv("AIRTABLE_PREARRIVAL_API_KEY")
                BASE_ID = os.getenv("AIRTABLE_PREARRIVAL_BASE_ID")
                TABLE_ID = "tblviNlbgLbdEalOj"

                url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
                headers = {
                    "Authorization": f"Bearer {AIRTABLE_TOKEN}"
                }

                response = requests.get(url, headers=headers)
                if response.status_code != 200:
                    return jsonify({
                        "error": "Failed to fetch upsell options",
                        "details": response.text
                    }), 500

                records = response.json().get("records", [])
                options = []
                for record in records:
                    fields = record.get("fields", {})
                    if not fields.get("active"):
                        continue
                    label = fields.get("label", "Option")
                    price = fields.get("price", "$—")
                    description = fields.get("description", "")
                    options.append(f"### {label} — **{price}**\n> {description}")

                upsell_text = (
                    "Here’s what I can offer before your stay kicks off:\n\n"
                    + "\n\n".join(options)
                    + "\n\nLet me know if you'd like me to pass any of these on to the host for you! 🌴"
                )

                # Log interest in Airtable
                airtable_url = f"https://api.airtable.com/v0/{os.getenv('AIRTABLE_BASE_ID')}/tblGEDhos73P2C5kn"
                log_headers = {
                    "Authorization": f"Bearer {os.getenv('AIRTABLE_API_KEY')}",
                    "Content-Type": "application/json"
                }

                log_data = {
                    "fields": {
                        "Name": name,
                        "Full Phone": phone,
                        "Date": date,
                        "Category": "request",
                        "Message": message,
                        "Reply": upsell_text,
                        "Log Type": "Prearrival Upsell"
                    }
                }

                log_response = requests.post(airtable_url, headers=log_headers, json=log_data)
                if log_response.status_code not in [200, 201]:
                    print(f"[Airtable] Upsell log failed: {log_response.text}")

                return jsonify({
                    "smartHandled": True,
                    "reply": upsell_text
                })

            except Exception as e:
                return jsonify({"error": "Upsell auto-reply failed", "details": str(e)}), 500

        # 🧠 Category classification + smart reply
        category = classify_category(message)
        reply = smart_response(category, emergency_phone)

        # Log normal message to Airtable
        airtable_url = f"https://api.airtable.com/v0/{os.getenv('AIRTABLE_BASE_ID')}/tblGEDhos73P2C5kn"
        headers = {
            "Authorization": f"Bearer {os.getenv('AIRTABLE_API_KEY')}",
            "Content-Type": "application/json"
        }

        log_types = detect_log_types(message)
        airtable_data = {
            "fields": {
                "Name": name,
                "Full Phone": phone,
                "Date": date,
                "Category": category,
                "Message": message,
                "Reply": reply,
                "Log Type": log_types
            }
        }

        response = requests.post(airtable_url, headers=headers, json=airtable_data)
        if response.status_code in [200, 201]:
            return jsonify({"success": True, "reply": reply}), 200
        else:
            return jsonify({"error": "Failed to save to Airtable", "details": response.text}), 500

    except FileNotFoundError:
        return jsonify({"error": "Unknown property"}), 400
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

