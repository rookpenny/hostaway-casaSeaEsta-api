import os
import json
import time
import logging
import requests
#import openai

from database import SessionLocal
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from database import engine, get_db

from fastapi import FastAPI, Request, Query, Path, HTTPException, Header, Form, APIRouter, Depends

from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError

from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from datetime import datetime, timedelta

from utils.config import load_property_config
from utils.hostaway import cached_token, fetch_reservations, find_upcoming_guest_by_code
from utils.message_helpers import classify_category, smart_response, detect_log_types
from utils.prearrival import prearrival_router
from utils.prearrival_debug import prearrival_debug_router
from utils.pms_sync import sync_all_pmcs
from apscheduler.schedulers.background import BackgroundScheduler
from uuid import uuid4
import uvicorn

from starlette.middleware.sessions import SessionMiddleware
from openai import OpenAI
from routes import admin, pmc_auth
from sqlalchemy.orm import Session
from models import Property, ChatSession, ChatMessage

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Config ---
#openai.api_key = os.getenv("OPENAI_API_KEY")


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


@app.post("/properties/{property_id}/chat")
def property_chat(property_id: int, payload: PropertyChatRequest, db: Session = Depends(get_db)):
    # 1) Load property & check Sandy status
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    pmc = prop.pmc
    if not (prop.sandy_enabled and pmc and pmc.active):
        return {"error": "Chat is offline for this property."}

    user_message = payload.message.strip()
    if not user_message:
        return {"error": "Empty message."}

    # 🔍 classify message
    category = classify_category(user_message)
    log_type = detect_log_types(user_message)
    sentiment = simple_sentiment(user_message)

    # 2) Get or create chat session
    session: ChatSession | None = None
    if payload.session_id:
        session = db.query(ChatSession).filter(
            ChatSession.id == payload.session_id,
            ChatSession.property_id == prop.id
        ).first()

    if not session:
        session = ChatSession(
            property_id=prop.id,
            source="guest_web",
        )
        db.add(session)
        db.flush()  # to get session.id

    session.last_activity_at = datetime.utcnow()

    # 3) Build context + prompt
    ctx = load_property_context(prop)
    system_prompt = build_system_prompt(prop, pmc, ctx)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    try:
        ai_response = client.chat.completions.create(
            model="gpt-4",
            temperature=0.7,
            messages=messages,
        )
        reply = ai_response.choices[0].message.content
    except Exception as e:
        db.add(ChatMessage(
            session=session,
            sender="guest",
            content=user_message,
            category=category,
            log_type=log_type,
            sentiment=sentiment,
        ))
        db.commit()
        return {"error": f"Sandy had an issue replying: {str(e)}"}

    # 4) Save messages
    db.add_all([
        ChatMessage(
            session=session,
            sender="guest",
            content=user_message,
            category=category,
            log_type=log_type,
            sentiment=sentiment,
        ),
        ChatMessage(
            session=session,
            sender="assistant",
            content=reply,
            category=None,
            log_type=None,
            sentiment="neutral",
        ),
    ])
    db.commit()

    return {
        "response": reply,
        "session_id": session.id,
        "category": category,
        "log_type": log_type,
        "sentiment": sentiment,
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

