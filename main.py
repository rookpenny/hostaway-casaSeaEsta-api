# ---- imports ----
import os
import json
import time
import logging
import requests
import uvicorn
import re

from typing import Optional
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
from routes import admin, pmc_auth

from starlette.middleware.sessions import SessionMiddleware
from database import SessionLocal, engine, get_db
from models import Property, ChatSession, ChatMessage, PMC

from utils.message_helpers import classify_category, smart_response, detect_log_types
from utils.pms_sync import sync_properties, sync_all_pmcs
from utils.pms_access import get_pms_access_info, ensure_pms_data
from utils.prearrival import prearrival_router
from utils.prearrival_debug import prearrival_debug_router
from utils.hostaway import get_upcoming_phone_for_listing


from apscheduler.schedulers.background import BackgroundScheduler
from openai import OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Init ---
app = FastAPI()  # ✅ Define app before using it
class ChatMessageIn(BaseModel):
    message: str
    
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

class ChatRequest(BaseModel):
    message: str
    
if __name__ == "__main__":
    try:
        uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
    except Exception as e:
        print(f"Error: {e}")


class PropertyChatRequest(BaseModel):
    message: str
    session_id: Optional[int] = None  # optional from frontend

class ChatRequest(BaseModel):
    message: str


@app.post("/properties/{property_id}/chat")
def property_chat(
    property_id: int,
    payload: PropertyChatRequest,
    db: Session = Depends(get_db)
):
    now = datetime.utcnow()

    # 0️⃣ Extract and validate user message
    user_message = (payload.message or "").strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Message is required")

    lowered = user_message.lower()

    # 1️⃣ Look up property + PMC, enforce Sandy enabled
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    pmc = db.query(PMC).filter(PMC.id == prop.pmc_id).first()
    if not pmc or not pmc.active or not prop.sandy_enabled:
        return {
            "response": (
                "Sandy is currently offline for this property 🌙\n\n"
                "Please contact your host directly for assistance."
            )
        }

    # 2️⃣ Create or reuse a ChatSession
    session: Optional[ChatSession] = None

    # Try to reuse explicit session_id from client if provided
    if payload.session_id:
        session = (
            db.query(ChatSession)
            .filter(
                ChatSession.id == payload.session_id,
                ChatSession.property_id == property_id,
            )
            .first()
        )

    # If none, fall back to last 4 hours
    if not session:
        recent_cutoff = now - timedelta(hours=4)
        session = (
            db.query(ChatSession)
            .filter(
                ChatSession.property_id == property_id,
                ChatSession.last_activity_at >= recent_cutoff,
            )
            .order_by(ChatSession.last_activity_at.desc())
            .first()
        )

    # If still none, create a new one
    if not session:
        session = ChatSession(
            property_id=property_id,
            source="guest_web",
            is_verified=False,
            created_at=now,
            last_activity_at=now,
        )
        db.add(session)
        db.commit()
        db.refresh(session)

    # 3️⃣ Attach PMS data (phone_last4 + reservation_id) for this session
    ensure_pms_data(db, session)  # -> updates ChatSession in Postgres

    # 4️⃣ Log guest message with intelligence fields
    category = classify_category(user_message)
    log_type = detect_log_types(user_message)
    sentiment = simple_sentiment(user_message)

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
    db.refresh(session)

    # 5️⃣ Door code logic (door code == last 4 of reservation phone)

    code_keywords = [
        "door code", "access code", "entry code", "pin", "key code", "lock code"
    ]
    is_code_request = any(k in lowered for k in code_keywords)

    # extract any 4-digit block they might have sent
    code_match = re.search(r"\b(\d{4})\b", user_message)
    provided_last4 = code_match.group(1) if code_match else None

    pms_last4 = session.phone_last4  # filled by ensure_pms_data (Hostaway)

    if is_code_request:
        # ✅ PMS has phone_last4 and guest provided matching last 4
        if pms_last4 and provided_last4 and provided_last4 == pms_last4:
            door_code = pms_last4  # by design: door code == last 4 digits of reservation phone
            reply_text = (
                f"**Your door code** 🔐\n\n"
                f"- Entry code: **{door_code}**\n"
                f"- This matches the last 4 digits of the phone number on your reservation.\n\n"
                "If the lock gives any trouble, try the code slowly and firmly, "
                "and contact your host if it still doesn’t work."
            )

        # 🔒 PMS has last4 but guest hasn’t proven it yet
        elif pms_last4 and not provided_last4:
            reply_text = (
                "I can help with your door code 🔐\n\n"
                "For security, please reply with the **last 4 digits of the phone number** "
                "on your reservation, and I’ll confirm your entry code."
            )

        # ❌ No PMS reservation / no phone_last4 available
        else:
            reply_text = (
                "I’m not seeing an active reservation linked to this chat yet, "
                "so I can’t safely share an access code. 😕\n\n"
                "Please double-check that you’re using the phone number on the booking, "
                "or contact your host directly for access help."
            )

        # Log assistant message for door-code branch
        assistant_msg = ChatMessage(
            session_id=session.id,
            sender="assistant",
            content=reply_text,
            created_at=datetime.utcnow(),
        )
        db.add(assistant_msg)
        db.commit()

        return {
            "response": reply_text,
            "session_id": session.id,
        }

    # 6️⃣ General LLM flow for non door-code messages

    # Load property-specific context from config/manual
    context = load_property_context(prop)
    system_prompt = build_system_prompt(prop, pmc, context)

    # Rebuild conversation history from DB
    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    messages = [{"role": "system", "content": system_prompt}]
    for m in history:
        role = "assistant" if m.sender == "assistant" else "user"
        messages.append({"role": role, "content": m.content})

    try:
        ai_response = client.chat.completions.create(
            model="gpt-4",
            temperature=0.7,
            messages=messages,
        )
        reply_text = ai_response.choices[0].message.content
    except Exception as e:
        print("[LLM ERROR in /properties/{property_id}/chat]:", e)
        reply_text = (
            "Oops, I ran into a technical issue while answering just now. 🐚\n\n"
            "Please try again in a moment, or contact your host directly if it’s urgent."
        )

    # Log assistant message for general replies
    assistant_msg = ChatMessage(
        session_id=session.id,
        sender="assistant",
        content=reply_text,
        created_at=datetime.utcnow(),
    )
    db.add(assistant_msg)
    db.commit()

    # 7️⃣ Response shape expected by chat.html
    return {
        "response": reply_text,
        "session_id": session.id,
    }


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

