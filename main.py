from fastapi import APIRouter


from datetime import datetime, timedelta

from fastapi import Header
from utils.config import load_property_config
from utils.smart import classify_category, smart_response, detect_log_types

from functools import lru_cache
from fastapi import Request, Query, Path
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from utils.config import load_property_config
from utils.message_helpers import classify_category, smart_response, detect_log_types  # assume you split helpers
from utils.hostaway import cached_token, fetch_reservations, find_upcoming_guest_by_code
from utils.prearrival import prearrival_router

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import os
import json
import time
import requests
import logging
from utils.airtable_client import (
    get_properties_table,
    get_pmcs_table,
    get_guests_table,
    get_prearrival_table,
    get_messages_table
)

app = FastAPI()
app.include_router(prearrival_router)


# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------ MODELS ------------------
class GuestMessage(BaseModel):
    name: str
    phone: str
    date: str
    message: str
    
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
#load_dotenv()
#app = Flask(__name__)
#CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

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

@app.get("/")
def root():
    return {"message": "Welcome to the multi-property Sandy API (FastAPI edition)!"}

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/properties")
def list_properties():
    table = get_properties_table()
    records = table.all()
    return [record["fields"] for record in records]


@app.get("/pmcs")
def list_pmcs():
    table = get_pmcs_table()
    records = table.all()
    return [record["fields"] for record in records]

@app.get("/guests")
def list_guests():
    table = get_guests_table()
    records = table.all()
    return [record["fields"] for record in records]

@app.get("/prearrival-options")
def get_prearrival_options():
    table = get_prearrival_table()
    records = table.all()
    return [record["fields"] for record in records if record["fields"].get("active")]

@app.post("/guest-message")
async def save_guest_message(message: GuestMessage, request: Request, property: str = Query("casa-sea-esta")):
    try:
        slug = property.lower().replace(" ", "-")
        config = load_property_config(slug)
        emergency_phone = config.get("emergency_phone", "N/A")

        name = message.name
        phone = message.phone
        date = message.date
        msg_text = message.message

        def matches_early_access_or_fridge(msg: str) -> bool:
            triggers = [
                "early access", "early check-in", "early checkin", "early arrival",
                "fridge stocking", "stock the fridge", "grocery drop",
                "fridge pre-stock", "can you stock", "groceries before arrival"
            ]
            return any(trigger in msg.lower() for trigger in triggers)

        if matches_early_access_or_fridge(msg_text):
            try:
                AIRTABLE_TOKEN = os.getenv("AIRTABLE_PREARRIVAL_API_KEY")
                BASE_ID = os.getenv("AIRTABLE_PREARRIVAL_BASE_ID")
                TABLE_ID = "tblviNlbgLbdEalOj"

                url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
                headers = {"Authorization": f"Bearer {AIRTABLE_TOKEN}"}
                response = requests.get(url, headers=headers)

                if response.status_code != 200:
                    return JSONResponse(status_code=500, content={"error": "Failed to fetch upsell options"})

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

                # Log to main guest messages table
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
                        "Message": msg_text,
                        "Reply": upsell_text,
                        "Log Type": "Prearrival Upsell"
                    }
                }
                log_url = f"https://api.airtable.com/v0/{os.getenv('AIRTABLE_BASE_ID')}/tblGEDhos73P2C5kn"
                requests.post(log_url, headers=log_headers, json=log_data)

                return {"smartHandled": True, "reply": upsell_text}

            except Exception as e:
                return JSONResponse(status_code=500, content={"error": "Upsell auto-reply failed", "details": str(e)})

        # Normal classification path
        category = classify_category(msg_text)
        reply = smart_response(category, emergency_phone)
        log_types = detect_log_types(msg_text)

        payload = {
            "fields": {
                "Name": name,
                "Full Phone": phone,
                "Date": date,
                "Category": category,
                "Message": msg_text,
                "Reply": reply,
                "Log Type": log_types
            }
        }

        airtable_url = f"https://api.airtable.com/v0/{os.getenv('AIRTABLE_BASE_ID')}/tblGEDhos73P2C5kn"
        headers = {
            "Authorization": f"Bearer {os.getenv('AIRTABLE_API_KEY')}",
            "Content-Type": "application/json"
        }

        response = requests.post(airtable_url, headers=headers, json=payload)
        if response.status_code not in [200, 201]:
            return JSONResponse(status_code=500, content={"error": "Failed to save to Airtable", "details": response.text})

        return {"success": True, "reply": reply}

    except FileNotFoundError:
        return JSONResponse(status_code=400, content={"error": "Unknown property"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": "Unexpected server error", "details": str(e)})


# Example: handle /docs/openapi.yaml
@app.get("/docs/openapi.yaml")
def serve_openapi():
    return FileResponse("docs/openapi.yaml", media_type="text/yaml")


# Example: handle /debug
@app.get("/debug", response_class=HTMLResponse)
def serve_debug_ui():
    with open("templates/debug.html") as f:
        return f.read()

# GET: /api/debug/property-config
@app.get("/api/debug/property-config")
def debug_property_config(property: str = Query("casa-sea-esta")):
    slug = property.lower().replace(" ", "-")
    try:
        config = load_property_config(slug)
        return config
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=404)
# GET: /some-endpoint
@app.get("/some-endpoint")
def some_endpoint(property: str = Query("casa-sea-esta")):
    slug = property.lower().replace(" ", "-")
    try:
        config = load_property_config(slug)
        listing_id = config.get("listing_id")
        emergency_phone = config.get("emergency_phone", "N/A")
        return {
            "property": config.get("property"),
            "listing_id": listing_id,
            "emergency_phone": emergency_phone
        }
    except FileNotFoundError:
        return JSONResponse(content={"error": f"Missing config for: {slug}"}, status_code=404)
    except Exception as e:
        return JSONResponse(content={"error": f"Config load error: {str(e)}"}, status_code=500)

# GET: /admin/config/{slug}
@app.get("/admin/config/{slug}")
def get_config(slug: str = Path(...)):
    try:
        config = load_property_config(slug)
        return config
    except FileNotFoundError:
        return JSONResponse(content={"error": "Config not found"}, status_code=404)

# POST: /api/refer
class ReferRequest(BaseModel):
    name: str
    phone: str


ALLOWED_STATUSES = {"new", "modified", "confirmed", "accepted", "ownerStay"}

@app.post("/api/refer")
def refer_friend(data: ReferRequest):
    try:
        airtable_url = f"https://api.airtable.com/v0/{os.getenv('AIRTABLE_BASE_ID')}/tblGEDhos73P2C5kn"
        headers = {
            "Authorization": f"Bearer {os.getenv('AIRTABLE_API_KEY')}",
            "Content-Type": "application/json"
        }
        payload = {
            "fields": {
                "Name": data.name,
                "Full Phone": data.phone,
                "Date": datetime.utcnow().strftime("%Y-%m-%d"),
                "Category": "referral",
                "Message": "Guest requested a referral link.",
                "Reply": "Referral link sent.",
                "Log Type": "Referral"
            }
        }

        response = requests.post(airtable_url, headers=headers, json=payload)
        if response.status_code not in [200, 201]:
            return JSONResponse(content={"error": "Failed to save referral log", "details": response.text}, status_code=500)

        referral_link = f"https://casaseaesta.com/referral?from={data.phone[-4:]}"
        return {
            "success": True,
            "message": "Here’s your referral link!",
            "link": referral_link
        }

    except Exception as e:
        return JSONResponse(content={"error": "Unexpected error", "details": str(e)}, status_code=500)
# POST: /api/join-email
class EmailOptInRequest(BaseModel):
    name: str
    phone: str
    email: str

@app.post("/api/join-email")
def join_email_list(data: EmailOptInRequest):
    try:
        airtable_url = f"https://api.airtable.com/v0/{os.getenv('AIRTABLE_BASE_ID')}/tblGEDhos73P2C5kn"
        headers = {
            "Authorization": f"Bearer {os.getenv('AIRTABLE_API_KEY')}",
            "Content-Type": "application/json"
        }

        payload = {
            "fields": {
                "Name": data.name,
                "Full Phone": data.phone,
                "Date": datetime.utcnow().strftime("%Y-%m-%d"),
                "Category": "email_opt_in",
                "Message": f"{data.name} ({data.email}) opted into the email list.",
                "Reply": "N/A",
                "Log Type": "Email List Opt-in"
            }
        }

        response = requests.post(airtable_url, headers=headers, json=payload)
        if response.status_code not in [200, 201]:
            return JSONResponse(content={"error": "Failed to log email opt-in", "details": response.text}, status_code=500)

        return {"success": True, "message": "You're on the list — welcome!"}

    except Exception as e:
        return JSONResponse(content={"error": "Unexpected error", "details": str(e)}, status_code=500)



@app.get("/api/debug/upcoming-guests")
def debug_upcoming_guests(
    request: Request,
    property: str = Query("casa-sea-esta"),
    days_out: int = Query(20),
    x_api_key: str = Header(None)
):
    expected_key = os.getenv("ADMIN_API_KEY")
    if x_api_key != expected_key:
        return JSONResponse(content={"error": "Unauthorized"}, status_code=403)

    try:
        slug = property.lower().replace(" ", "-")

        try:
            config = load_property_config(slug)
        except FileNotFoundError:
            return JSONResponse(content={"error": f"No config found for '{slug}'"}, status_code=404)

        listing_id = config.get("listing_id")
        if not listing_id:
            return JSONResponse(content={"error": "Missing listing_id in config"}, status_code=400)

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

        return {"results": guests, "count": len(guests)}

    except Exception as e:
        return JSONResponse(content={"error": "Unexpected error", "details": str(e)}, status_code=500)

@app.get("/api/guest")
def get_guest_info(property: str = Query("casa-sea-esta")):
    slug = property.lower().replace(" ", "-")

    try:
        config = load_property_config(slug)
    except FileNotFoundError:
        return JSONResponse(content={"error": f"No config found for '{slug}'"}, status_code=404)
    except Exception as e:
        return JSONResponse(content={"error": f"Failed to load config: {str(e)}"}, status_code=500)

    try:
        listing_id = config.get("listing_id")
        if not listing_id:
            return JSONResponse(content={"error": "Missing listing_id in config"}, status_code=400)

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
            return JSONResponse(content={"message": "No guest currently checked in."}, status_code=404)

        latest = max(valid_reservations, key=lambda r: r.get("updatedOn", ""))
        return {
            "guestName": latest.get("guestName"),
            "checkIn": latest.get("arrivalDate"),
            "checkInTime": str(latest.get("checkInTime", config.get("default_checkin_time", 16))),
            "checkOut": latest.get("departureDate"),
            "checkOutTime": str(latest.get("checkOutTime", config.get("default_checkout_time", 10))),
            "numberOfGuests": str(latest.get("numberOfGuests")),
            "phone": latest.get("phone"),
            "notes": latest.get("comment", "")
        }

    except Exception as e:
        return JSONResponse(content={"error": "Unexpected server error", "details": str(e)}, status_code=500)
        

@app.get("/api/guest-authenticated")
def guest_authenticated(
    code: str = Query(...),
    property: str = Query("casa-sea-esta")
):
    slug = property.lower().replace(" ", "-")

    if not code.isdigit():
        return JSONResponse(content={"error": "Invalid code format"}, status_code=400)

    try:
        config = load_property_config(slug)
    except FileNotFoundError:
        return JSONResponse(content={"error": f"No config found for '{slug}'"}, status_code=404)

    listing_id = config.get("listing_id")
    property_name = config.get("property", slug.replace("-", " ").title())
    emergency_phone = config.get("emergency_phone", "N/A")

    if not listing_id:
        return JSONResponse(content={"error": "Missing listing_id in config"}, status_code=400)

    try:
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
                return {
                    "guestName": guest_name,
                    "phone": phone,
                    "property": property_name,
                    "checkIn": check_in,
                    "checkOut": check_out,
                    "message": f"You're all set, {guest_name} — welcome to {property_name}! 🌴\n"
                               "Need local recs, help with the house, or want to extend your stay? I’ve got you covered! ☀️",
                    "verified": True
                }

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

            return {
                "guestName": guest["name"],
                "phone": guest["phone"],
                "property": guest["property"],
                "checkIn": guest["checkin_date"],
                "checkOut": guest["checkout_date"],
                "message": (
                    f"Hey hey! It looks like your stay hasn’t kicked off just yet, so I can’t verify you until check-in day @ 4pm. 🕓\n"
                    "BUT — I’d love to help you get ready! Want to know:\n"
                    "- ✅ What to expect on arrival\n"
                    "- 🏡 How check-in works\n"
                    "- 📍Want early access or a pre-stocked fridge?\n"
                    "Just say the word!"
                ),
                "prearrival": True
            }

        return JSONResponse(content={"error": "Guest not found or not currently staying"}, status_code=401)

    except Exception as e:
        return JSONResponse(content={"error": "Unexpected server error", "details": str(e)}, status_code=500)

router = APIRouter()

@router.get("/api/prearrival-options")
def prearrival_options(phone: str = Query(...)):
    try:
        # ✅ Airtable config
        AIRTABLE_TOKEN = os.getenv("AIRTABLE_PREARRIVAL_API_KEY")
        BASE_ID = os.getenv("AIRTABLE_PREARRIVAL_BASE_ID")
        TABLE_ID = "tblviNlbgLbdEalOj"  # Hardcoded table ID

        url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
        headers = {
            "Authorization": f"Bearer {AIRTABLE_TOKEN}"
        }

        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return JSONResponse(
                status_code=500,
                content={"error": "Failed to fetch from Airtable", "details": response.text}
            )

        records = response.json().get("records", [])
        options = []

        for record in records:
            fields = record.get("fields", {})
            if not fields.get("active"):
                continue

            options.append({
                "id": fields.get("id"),
                "label": fields.get("label"),
                "description": fields.get("description"),
                "price": fields.get("price")
            })

        return {"options": options}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "Unexpected error", "details": str(e)}
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

