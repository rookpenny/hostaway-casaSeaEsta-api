# ---- imports ----
import os
import json
import time
import logging
import requests
import uvicorn
import re

from datetime import datetime, timedelta
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fastapi import (
    FastAPI, Request, Query, Path, HTTPException, Header, Form,
    APIRouter, Depends
)
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from starlette.middleware.sessions import SessionMiddleware

from database import SessionLocal, engine, get_db
from models import Property, ChatSession, ChatMessage, PMC
from utils.message_helpers import classify_category, smart_response, detect_log_types
from utils.pms_sync import sync_properties, sync_all_pmcs

from routes import admin, pmc_auth
from utils.prearrival import prearrival_router
from utils.prearrival_debug import prearrival_debug_router
from apscheduler.schedulers.background import BackgroundScheduler
from openai import OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Init ---
app = FastAPI()  # ✅ Define app before using it

# --- Routers ---
app.include_router(admin.router)
app.include_router(pmc_auth.router)
app.include_router(prearrival_router)
app.include_router(prearrival_debug_router)

# Middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET") or "fallbacksecret"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static + Templates
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print("❌ Validation Error:")
    print("➡️ Raw body:", await request.body())
    print("➡️ Errors:", exc.errors())
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()}
    )
    
# --- Startup Jobs ---
def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(sync_all_pmcs, "interval", hours=24)
    scheduler.start()

# --- DB Connection Test ---
try:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        print("✅ Database connected successfully.")
except SQLAlchemyError as e:
    print(f"❌ Database connection failed: {e}")


start_scheduler()

# --- Sync Trigger ---
@app.post("/admin/sync-properties")
def manual_sync():
    try:
        count = sync_all_pmcs()
        return HTMLResponse(
            f"<h2>Synced {count} properties across all PMCs.</h2>"
            "<a href='/admin/dashboard'>Back to Dashboard</a>"
        )
    except Exception as e:
        return HTMLResponse(
            f"<h2>Sync failed: {str(e)}</h2>"
            "<a href='/admin/dashboard'>Back to Dashboard</a>",
            status_code=500
        )

# --- Root Health Check ---
@app.get("/")
def root():
    return {"message": "Welcome to the multi-property Sandy API (FastAPI edition)!"}

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/routes")
def list_routes():
    return [{"path": route.path, "methods": list(route.methods)} for route in app.router.routes]

# Additional routes (e.g., /properties, /guests, /guest-message, etc.)
# are handled and correct as provided in your current file

# --- Chat Endpoint ---
class ChatRequest(BaseModel):
    message: str

@app.post("/chat")
def chat(request: ChatRequest):
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": request.message}
            ]
        )
        return {"response": response.choices[0].message.content}
    except Exception as e:
        return {"error": str(e)}

@app.get("/debug/properties")
def debug_properties(db: Session = Depends(get_db)):
    props = db.query(Property).all()
    return [
        {
            "id": p.id,
            "property_name": p.property_name,
            "pms_property_id": p.pms_property_id,
            "sandy_enabled": p.sandy_enabled,
            "pmc_id": p.pmc_id,
        }
        for p in props
    ]

@app.get("/guest/{property_id}", response_class=HTMLResponse)
def guest_chat_ui(request: Request, property_id: int, db: Session = Depends(get_db)):
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    pmc = prop.pmc
    is_live = bool(prop.sandy_enabled and pmc and pmc.active)

    return templates.TemplateResponse(
        "chat.html",
        {
            "request": request,
            "property_id": prop.id,
            "property_name": prop.property_name,
            "is_live": is_live,
        },
    )



# --- Start Server ---
if __name__ == "__main__":
    try:
        uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
    except Exception as e:
        print(f"Error: {e}")


from pydantic import BaseModel

class PropertyChatRequest(BaseModel):
    message: str
    session_id: int | None = None

class ChatRequest(BaseModel):
    message: str


@app.post("/properties/{property_id}/chat")
def property_chat(
    property_id: int,
    payload: ChatRequest,
    db: Session = Depends(get_db)
):
    user_message = (payload.message or "").strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Message is required")

    # 1️⃣ Look up property + PMC, enforce "Sandy enabled"
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    pmc = db.query(PMC).filter(PMC.id == prop.pmc_id).first()

    if not pmc or not pmc.active or not prop.sandy_enabled:
        # Graceful "offline" message for this property
        return {
            "response": (
                "Sandy is currently offline for this property 🌙\n\n"
                "Please contact your host directly for assistance."
            )
        }

    # 2️⃣ Load property config (door code, phone last4, check-in, etc.)
    try:
        # Adjust this to match your actual load_property_config signature
        # For now we assume it can take a Property or property_id
        ctx_config = load_property_config(property_id)
    except Exception:
        ctx_config = {}

    door_code = ctx_config.get("door_code")
    expected_phone_last4 = ctx_config.get("guest_phone_last4")
    checkin_time = ctx_config.get("checkin_time", "16:00")  # "HH:MM"
    checkout_time = ctx_config.get("checkout_time", "11:00")

    # 3️⃣ Find or create a ChatSession for this property
    now = datetime.utcnow()

    # Reuse most recent session within the last 4 hours
    recent_cutoff = now - timedelta(hours=4)
    session = (
        db.query(ChatSession)
        .filter(
            ChatSession.property_id == property_id,
            ChatSession.last_activity_at >= recent_cutoff
        )
        .order_by(ChatSession.last_activity_at.desc())
        .first()
    )

    if not session:
        session = ChatSession(
            property_id=property_id,
            source="web_chat",
            is_verified=False,
            created_at=now,
            last_activity_at=now,
        )
        db.add(session)
        db.commit()
        db.refresh(session)

    # 4️⃣ Log the guest's message with basic categorisation
    category = classify_category(user_message)
    log_type = detect_log_types(user_message)

    # Very simple sentiment stub; you can replace with a real model later
    lowered = user_message.lower()
    if any(word in lowered for word in ["angry", "upset", "terrible", "horrible", "bad", "not happy"]):
        sentiment = "negative"
    elif any(word in lowered for word in ["love", "great", "amazing", "awesome", "perfect", "thank you"]):
        sentiment = "positive"
    else:
        sentiment = "neutral"

    guest_msg = ChatMessage(
        session_id=session.id,
        sender="guest",
        content=user_message,
        category=category,
        log_type=log_type,
        sentiment=sentiment,
        created_at=now,
    )
    db.add(guest_msg)
    session.last_activity_at = now
    db.commit()

    # 5️⃣ Special handling: access code + verification flow
    code_keywords = ["door code", "access code", "entry code", "pin", "key code"]
    is_code_request = any(k in lowered for k in code_keywords)

    # 5a) If user is asking for the code but not verified yet → prompt for last 4 digits
    if is_code_request and not session.is_verified:
        return {
            "response": (
                "For security, I can only share your access code after I confirm your identity. 🔐\n\n"
                "**What are the last 4 digits of the phone number** on your reservation? 📱"
            ),
            "session_id": session.id
        }

    # 5b) If the message looks like exactly 4 digits and session not verified yet → treat as verification attempt
    four_digits = re.fullmatch(r"\d{4}", user_message)
    if four_digits and not session.is_verified:
        if expected_phone_last4 and expected_phone_last4 == user_message:
            session.is_verified = True
            session.phone_last4 = user_message
            session.last_activity_at = now
            db.commit()
            return {
                "response": (
                    "Thank you! You're verified 🎉\n\n"
                    "You can now ask me for your **door code** or anything else you need."
                ),
                "session_id": session.id
            }
        else:
            return {
                "response": (
                    "Hmm, that doesn’t match what I have on file. 😕\n\n"
                    "Please double-check the **last 4 digits** of the phone number on your reservation, "
                    "or reach out to your host directly if you think there’s an issue."
                ),
                "session_id": session.id
            }

    # 5c) If guest is verified and asks for the code → check timing & reveal or upsell early check-in
    if is_code_request and session.is_verified:
        if not door_code:
            # No code configured → fail safe
            return {
                "response": (
                    "You're verified 🎉 but I don't have an access code configured for this property yet.\n\n"
                    "Please contact your host directly so they can share it with you."
                ),
                "session_id": session.id
            }

        # Very simple time gating: compare current UTC hour with configured check-in hour
        try:
            checkin_hour = int(checkin_time.split(":")[0])
        except Exception:
            checkin_hour = 16  # default 4pm

        now_hour = now.hour  # later you can adjust for property timezone

        if now_hour < checkin_hour:
            # Too early – offer early check-in
            return {
                "response": (
                    "You're verified 🎉 but it's a bit early for check-in.\n\n"
                    f"Your standard check-in time is **{checkin_time}**.\n\n"
                    "🌟 If you'd like, I can check on **early check-in** options for you."
                ),
                "session_id": session.id
            }

        # OK to reveal the code
        return {
            "response": f"You're all set! 🎉\n\nYour access code is: **{door_code}** 🔓",
            "session_id": session.id
        }

    # 6️⃣ Fallback to normal Sandy AI reply for everything else
    # Build system prompt with property context
    system_prompt = (
        "You are Sandy, a beachy, upbeat AI concierge for a vacation rental.\n"
        "Always respond in the same language as the guest.\n"
        "Use markdown with:\n"
        "- Bold section headings\n"
        "- Bullet points\n"
        "- Short paragraphs\n"
        "- Emojis where helpful\n\n"
        f"This message is for property: {prop.property_name} (ID: {prop.id})."
    )

    try:
        ai_response = client.chat.completions.create(
            model="gpt-4",
            temperature=0.8,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        reply_text = ai_response.choices[0].message.content.strip()
    except Exception as e:
        reply_text = (
            "Oops, I had a little trouble thinking just now 🧠💭\n\n"
            "Please try again in a moment, or contact your host directly if it's urgent."
        )
        logging.exception("Error calling OpenAI in property_chat: %s", e)

    # 7️⃣ Log assistant reply
    assistant_msg = ChatMessage(
        session_id=session.id,
        sender="assistant",
        content=reply_text,
        created_at=datetime.utcnow(),
    )
    db.add(assistant_msg)
    session.last_activity_at = datetime.utcnow()
    db.commit()

    return {
        "response": reply_text,
        "session_id": session.id
    }


def get_pms_access_info(pmc: PMC, prop: Property):
    """
    Fetch access info (phone_last4, door_code, reservation_id) for this property
    from the configured PMS.

    Right now we implement Hostaway; others can be added later.
    Door code = last 4 digits of the guest's phone number.
    """
    try:
        pms = (pmc.pms_integration or "").lower()

        if pms == "hostaway":
            listing_id = prop.pms_property_id
            if not listing_id:
                return None, None, None

            phone_last4, full_phone, reservation_id = get_upcoming_phone_for_listing(listing_id)

            if not phone_last4:
                return None, None, None

            # Door code is the last 4 digits of the phone
            door_code = phone_last4
            return phone_last4, door_code, reservation_id

        # TODO: add Guesty / other PMS later
        return None, None, None

    except Exception as e:
        logging.exception("Error fetching PMS access info: %s", e)
        return None, None, None


def simple_sentiment(message: str) -> str:
    text = message.lower()
    negative_markers = ["terrible", "awful", "angry", "bad", "disappointed", "upset"]
    positive_markers = ["great", "amazing", "awesome", "love", "fantastic", "perfect"]

    if any(w in text for w in negative_markers):
        return "negative"
    if any(w in text for w in positive_markers):
        return "positive"
    return "neutral"


def load_property_context(prop: Property) -> dict:
    config = {}
    manual_text = ""

    base_dir = prop.data_folder_path or ""
    if base_dir:
        config_path = os.path.join(base_dir, "config.json")
        manual_path = os.path.join(base_dir, "manual.txt")

        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception:
                config = {}

        if os.path.exists(manual_path):
            try:
                with open(manual_path, "r", encoding="utf-8") as f:
                    manual_text = f.read()
            except Exception:
                manual_text = ""

    return {"config": config, "manual": manual_text}


def build_system_prompt(prop: Property, pmc, context: dict) -> str:
    config = context.get("config", {})
    manual = context.get("manual", "")

    house_rules = config.get("house_rules") or ""
    wifi = config.get("wifi") or {}
    wifi_info = ""
    if isinstance(wifi, dict):
        wifi_info = f"WiFi network: {wifi.get('ssid', '')}, password: {wifi.get('password', '')}"

    emergency_phone = config.get("emergency_phone") or (pmc.main_contact if pmc else "")

    return f"""
You are Sandy, a beachy, upbeat AI concierge for a vacation rental called "{prop.property_name}".

Property host/manager: {pmc.pmc_name if pmc else "Unknown PMC"}.
Emergency or urgent issues should be directed to: {emergency_phone} (phone).

Always:
- Answer in the SAME language the guest uses.
- Use clear, friendly, warm tone with light emojis.
- Use markdown formatting: **bold headers**, bullet points, and line breaks.
- If you reference locations, include Google Maps links when possible.

Important property info:
- House rules: {house_rules}
- WiFi: {wifi_info}
- Other details from the house manual are below.

House manual:
\"\"\"
{manual}
\"\"\"

If you don't know something, say you aren't sure and suggest the guest contact the host.
Never make up access codes or sensitive details that are not explicitly in the config/manual.
""".strip()

