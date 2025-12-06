# ---- imports ----
import os
import json
import time
import logging
import requests
import uvicorn
import re
import stripe

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

from typing import Optional
from datetime import datetime, timedelta
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fastapi import (
    FastAPI, Request, Query, Path, HTTPException, Header, Form,
    APIRouter, Depends, status   # 👈 added status
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
from models import Property, ChatSession, ChatMessage, PMC, Upgrade, Reservation

from utils.message_helpers import classify_category, smart_response, detect_log_types
from utils.pms_sync import sync_properties, sync_all_pmcs
from utils.pms_access import get_pms_access_info, ensure_pms_data
from utils.prearrival import prearrival_router
from utils.prearrival_debug import prearrival_debug_router
from utils.hostaway import get_upcoming_phone_for_listing, get_listing_overview

from apscheduler.schedulers.background import BackgroundScheduler

from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Init ---

app = FastAPI()

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
def guest_app_ui(request: Request, property_id: int, db: Session = Depends(get_db)):
    # store the property ID so logout can redirect correctly
    request.session["last_property"] = property_id
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    pmc = prop.pmc
    is_live = bool(prop.sandy_enabled and pmc and pmc.active)

    # Optional: load config/manual for WiFi, times, images, etc.
    context = load_property_context(prop)
    cfg = context.get("config", {}) or {}
    wifi = cfg.get("wifi") or {}

    # Base values from config.json (if present)
    address = cfg.get("address")
    city_name = cfg.get("city_name")
    hero_image_url = cfg.get("hero_image_url")
    experiences_hero_url = cfg.get("experiences_hero_url")

    # If this is a Hostaway property and we have PMC creds, pull from Hostaway
    if (
        pmc
        and pmc.pms_integration
        and pmc.pms_integration.lower() == "hostaway"
        and prop.pms_integration
        and prop.pms_integration.lower() == "hostaway"
        and prop.pms_property_id
        and pmc.pms_api_key
        and pmc.pms_api_secret
    ):
        try:
            hero, ha_address, ha_city = get_listing_overview(
                listing_id=str(prop.pms_property_id),
                client_id=pmc.pms_api_key,
                client_secret=pmc.pms_api_secret,
            )

            # Only override if config didn't already specify these
            if hero and not hero_image_url:
                hero_image_url = hero
                if not experiences_hero_url:
                    experiences_hero_url = hero

            if ha_address and not address:
                address = ha_address

            if ha_city and not city_name:
                city_name = ha_city

        except Exception as e:
            print("[Hostaway] Failed to fetch listing overview:", e)

    # Final fallback: if no separate guides hero, reuse main hero
    if not experiences_hero_url and hero_image_url:
        experiences_hero_url = hero_image_url

    # 🔹 Pull latest ChatSession that has guest_name / dates from PMS, if any
    latest_session = (
        db.query(ChatSession)
        .filter(ChatSession.property_id == prop.id)
        .order_by(ChatSession.last_activity_at.desc())
        .first()
    )

    reservation_name = latest_session.guest_name if latest_session and latest_session.guest_name else None
    arrival_date_db = latest_session.arrival_date if latest_session and latest_session.arrival_date else None
    departure_date_db = latest_session.departure_date if latest_session and latest_session.departure_date else None

    # Optional: build a Google Maps link from address
    from urllib.parse import quote_plus
    google_maps_link = None
    if address or city_name:
        q = " ".join(filter(None, [address, city_name]))
        google_maps_link = f"https://www.google.com/maps/search/?api=1&query={quote_plus(q)}"

    # ------------------------------------------------------------
    # 🔹 SAME-DAY TURNOVER LOGIC (backed by Reservation table)
    # ------------------------------------------------------------
    same_day_turnover = False
    hide_time_flex = False

    try:
        # 1) Try to find "today's" active reservation
        today_res = get_today_reservation(db, prop.id)

        # 2) Prefer using the Reservation table
        active_dep_date = None
        if today_res and today_res.departure_date:
            active_dep_date = today_res.departure_date

        # 3) Fallback: use departure_date from ChatSession if we don't have a Reservation dep date
        if not active_dep_date and departure_date_db:
            from datetime import date as _date_type

            if isinstance(departure_date_db, _date_type):
                active_dep_date = departure_date_db
            elif isinstance(departure_date_db, datetime):
                active_dep_date = departure_date_db.date()
            else:
                # expect string like "2025-12-06"
                try:
                    active_dep_date = datetime.fromisoformat(str(departure_date_db)).date()
                except Exception:
                    active_dep_date = None

        # 4) If we have a departure date, check if another reservation arrives that same day
        if active_dep_date:
            q = db.query(Reservation).filter(
                Reservation.property_id == prop.id,
                Reservation.arrival_date == active_dep_date,
            )

            # if we know today's reservation, don't count it as "another"
            if today_res:
                q = q.filter(Reservation.id != today_res.id)

            overlap_count = q.count()
            if overlap_count > 0:
                same_day_turnover = True
                hide_time_flex = True

    except Exception as e:
        print("[UPGRADES] Error computing same_day_turnover:", e)
        same_day_turnover = False
        hide_time_flex = False

    # ------------------------------------------------------------
    # 🔹 Load upgrades for this property
    # ------------------------------------------------------------
    upgrades = (
        db.query(Upgrade)
        .filter(
            Upgrade.property_id == prop.id,
            Upgrade.is_active == True,
        )
        .order_by(Upgrade.id.asc())
        .all()
    )

    # Convert DB objects → safe dicts for template or JS
    upgrades_payload = []
    for up in upgrades:
        upgrades_payload.append(
            {
                "id": up.id,
                "slug": up.slug,
                "title": getattr(up, "title", None),
                "short_description": getattr(up, "short_description", None),
                "long_description": getattr(up, "long_description", None),
                "price_cents": getattr(up, "price_cents", None),
                "price_currency": getattr(up, "currency", "usd"),
                "stripe_price_id": getattr(up, "stripe_price_id", None),
                "image_url": getattr(up, "image_url", None),
                # used in the template as up.price_display
                "price_display": getattr(up, "price_display", None)
                if hasattr(up, "price_display")
                else None,
            }
        )

    # ------------------------------------------------------------
    # 🔹 Render Template
    # ------------------------------------------------------------
    return templates.TemplateResponse(
        "guest_app.html",
        {
            "request": request,
            "property_id": prop.id,
            "property_name": prop.property_name,
            "reservation_name": reservation_name,
            "property_address": address,
            "wifi_ssid": wifi.get("ssid"),
            "wifi_password": wifi.get("password"),
            "checkin_time": cfg.get("checkin_time"),
            "checkout_time": cfg.get("checkout_time"),

            # PMS dates override config if present
            "arrival_date": arrival_date_db or cfg.get("arrival_date"),
            "departure_date": departure_date_db or cfg.get("departure_date"),

            "feature_image_url": cfg.get("feature_image_url"),
            "family_image_url": cfg.get("family_image_url"),
            "foodie_image_url": cfg.get("foodie_image_url"),

            "city_name": city_name,
            "hero_image_url": hero_image_url,
            "experiences_hero_url": experiences_hero_url,
            "google_maps_link": google_maps_link,
            "is_live": is_live,
            "is_verified": request.session.get(f"guest_verified_{property_id}", False),

            # Upgrades + turnover flags
            "upgrades": upgrades_payload,
            "same_day_turnover": same_day_turnover,
            "hide_time_flex": hide_time_flex,
        },
    )



def compute_same_day_turnover(db: Session, property_id: int, reservation: Reservation | None) -> bool:
    """
    Returns True if there is ANOTHER reservation arriving on the same day
    this reservation checks out.
    """
    if not reservation or not reservation.departure_date:
        return False

    checkout = reservation.departure_date

    next_guest = (
        db.query(Reservation)
        .filter(
            Reservation.property_id == property_id,
            Reservation.arrival_date == checkout,
            Reservation.id != reservation.id,
        )
        .first()
    )

    return next_guest is not None


def should_hide_upgrade_for_turnover(upgrade: Upgrade, same_day_turnover: bool) -> bool:
    """
    If it's a same-day turnover AND this upgrade is early check-in / late checkout,
    we hide it.
    """
    if not same_day_turnover:
        return False

    title = (upgrade.title or "").lower()

    # tweak these matches to match your real upgrade titles
    early_phrases = [
        "early check-in",
        "early check in",
        "early arrival",
    ]
    late_phrases = [
        "late checkout",
        "late check-out",
        "late check out",
        "late departure",
    ]

    if any(p in title for p in early_phrases + late_phrases):
        return True

    return False


@app.get("/guest/{property_id}", name="guest_app")
async def guest_app(
    property_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Render the guest web app (guest_app.html).
    This is where we:
      - load property + reservation
      - compute same_day_turnover
      - filter upgrades based on same_day_turnover
    """

    # --- Load property ---
    property_obj = (
        db.query(Property)
        .filter(Property.id == property_id)
        .first()
    )
    if not property_obj:
        raise HTTPException(status_code=404, detail="Property not found")

    # --- Load current reservation for this guest ---
    # 👉 Replace this with your real logic (e.g. using a code, session, cookie, etc.)
    reservation: Reservation | None = (
        db.query(Reservation)
        .filter(
            Reservation.property_id == property_id,
            # put your real "current guest" conditions here
        )
        .first()
    )

    # --- Compute same-day turnover flag ---
    same_day_turnover = compute_same_day_turnover(
        db=db,
        property_id=property_id,
        reservation=reservation,
    )

    # --- Load upgrades for this property ---
    # Adjust this query to match your schema
    upgrades = (
        db.query(Upgrade)
        .filter(Upgrade.property_id == property_id)
        .order_by(Upgrade.sort_order.asc())
        .all()
    )

    # --- Filter upgrades for same-day turnover (hide early/late) ---
    visible_upgrades = [
        up for up in upgrades
        if not should_hide_upgrade_for_turnover(up, same_day_turnover)
    ]

    # --- Compute template fields you already use in guest_app.html ---
    reservation_name = reservation.guest_name if reservation else None
    arrival_date = reservation.arrival_date if reservation else None
    departure_date = reservation.departure_date if reservation else None

    # You probably already compute these somewhere — keep your versions
    wifi_ssid = property_obj.wifi_ssid if hasattr(property_obj, "wifi_ssid") else None
    wifi_password = property_obj.wifi_password if hasattr(property_obj, "wifi_password") else None
    checkin_time = getattr(property_obj, "checkin_time", None)
    checkout_time = getattr(property_obj, "checkout_time", None)

    hero_image_url = getattr(property_obj, "hero_image_url", None)
    default_image_url = "/static/img/default-hero.jpg"

    experiences_hero_url = getattr(property_obj, "experiences_hero_url", hero_image_url)
    feature_image_url = getattr(property_obj, "feature_image_url", None)
    family_image_url = getattr(property_obj, "family_image_url", None)
    foodie_image_url = getattr(property_obj, "foodie_image_url", None)

    context = {
        "request": request,
        "property_id": property_obj.id,
        "property_name": property_obj.name,
        "property_address": property_obj.address,
        "reservation_name": reservation_name,
        "arrival_date": arrival_date,
        "departure_date": departure_date,
        "wifi_ssid": wifi_ssid,
        "wifi_password": wifi_password,
        "checkin_time": checkin_time,
        "checkout_time": checkout_time,
        "hero_image_url": hero_image_url,
        "default_image_url": default_image_url,
        "experiences_hero_url": experiences_hero_url,
        "feature_image_url": feature_image_url,
        "family_image_url": family_image_url,
        "foodie_image_url": foodie_image_url,

        # 👇 IMPORTANT: pass filtered upgrades + turnover flag
        "upgrades": visible_upgrades,
        "same_day_turnover": same_day_turnover,
    }

    return templates.TemplateResponse("guest_app.html", context)




class VerifyRequest(BaseModel):
    code: str


@app.post("/guest/{property_id}/verify-json")
def verify_json(
    property_id: int,
    payload: VerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    code = (payload.code or "").strip()

    # 1) Quick format check
    if not code.isdigit() or len(code) != 4:
        return JSONResponse(
            {"success": False, "error": "Please enter exactly 4 digits."},
            status_code=400,
        )

    # 2) TEST OVERRIDE (for your own testing)
    test_code = os.getenv("TEST_UNLOCK_CODE")
    if test_code and code == test_code:
        # Mark this browser as verified
        request.session[f"guest_verified_{property_id}"] = True

        # Fake but realistic-looking guest meta for the UI
        today = datetime.utcnow().date()
        arrival = today.strftime("%Y-%m-%d")
        departure = (today + timedelta(days=3)).strftime("%Y-%m-%d")

        return {
            "success": True,
            "guest_name": "Test Guest",
            "arrival_date": arrival,
            "departure_date": departure,
        }

    # 3) Load property + PMC
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    pmc = prop.pmc
    if not pmc:
        return JSONResponse(
            {"success": False, "error": "This property is not linked to a PMS."},
            status_code=400,
        )

    # 4) Get PMS-linked last 4 digits + guest info
    try:
        # For Hostaway, use "upcoming or in-house" reservation lookup
        if (
            pmc.pms_integration
            and pmc.pms_integration.lower() == "hostaway"
            and prop.pms_integration
            and prop.pms_integration.lower() == "hostaway"
            and prop.pms_property_id
            and pmc.pms_api_key
            and pmc.pms_api_secret
        ):
            (
                phone_last4,
                door_code,
                reservation_id,
                guest_name,
                arrival_date,
                departure_date,
            ) = get_upcoming_phone_for_listing(
                listing_id=str(prop.pms_property_id),
                client_id=pmc.pms_api_key,
                client_secret=pmc.pms_api_secret,
            )
        else:
            # fallback for other PMS types
            (
                phone_last4,
                door_code,
                reservation_id,
                guest_name,
                arrival_date,
                departure_date,
            ) = get_pms_access_info(pmc, prop)

    except Exception as e:
        print("[VERIFY PMS ERROR]", e)
        return JSONResponse(
            {
                "success": False,
                "error": "Could not verify your reservation. Please try again.",
            },
            status_code=500,
        )

    # 5) Enforce a 30-day arrival window
    WINDOW_DAYS = 30
    def parse_ymd(d: Optional[str]):
        if not d:
            return None
        try:
            # PMS dates are typically "YYYY-MM-DD"
            return datetime.strptime(d, "%Y-%m-%d").date()
        except Exception:
            return None

    today = datetime.utcnow().date()
    arrival_date_obj = parse_ymd(arrival_date)

    if arrival_date_obj is not None:
        # if arrival is more than 30 days in the future, block unlock
        if arrival_date_obj > today + timedelta(days=WINDOW_DAYS):
            return JSONResponse(
                {
                    "success": False,
                    "error": (
                        "You can only unlock this stay within "
                        f"{WINDOW_DAYS} days of arrival."
                    ),
                },
                status_code=400,
            )


    # 5) No upcoming/current reservation / phone found
    if not phone_last4 or not reservation_id:
        return JSONResponse(
            {
                "success": False,
                "error": "No upcoming reservation found for this property.",
            },
            status_code=400,
        )

    # 6) Wrong code → deny access
    if code != phone_last4:
        return JSONResponse(
            {
                "success": False,
                "error": "That code does not match the reservation phone number.",
            },
            status_code=403,
        )

    # 7) Correct: mark this browser as verified and return guest + stay info
    request.session[f"guest_verified_{property_id}"] = True

    return {
        "success": True,
        "guest_name": guest_name,
        "arrival_date": arrival_date,
        "departure_date": departure_date,
    }




class PropertyChatRequest(BaseModel):
    message: str
    session_id: Optional[int] = None
    language: Optional[str] = None



@app.get("/manifest/{property_id}.webmanifest")
def dynamic_manifest(property_id: int, request: Request, db: Session = Depends(get_db)):
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    return templates.TemplateResponse(
        "manifest.webmanifest",
        {
            "request": request,
            "property_id": property_id,
            "property_name": prop.property_name,
        },
        media_type="application/manifest+json",
    )


class UpgradeCheckoutRequest(BaseModel):
    guest_email: Optional[str] = None


@app.post("/properties/{property_id}/upgrades/{upgrade_id}/checkout")
def create_upgrade_checkout(
    property_id: int,
    upgrade_id: int,
    payload: UpgradeCheckoutRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    # Enforce that the guest has unlocked this stay
    verified_flag = request.session.get(f"guest_verified_{property_id}", False)
    if not verified_flag:
        raise HTTPException(
            status_code=403,
            detail="Please unlock your stay before purchasing upgrades.",
        )

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    upgrade = (
        db.query(Upgrade)
        .filter(
            Upgrade.id == upgrade_id,
            Upgrade.property_id == property_id,
            Upgrade.is_active == True,
        )
        .first()
    )
    if not upgrade:
        raise HTTPException(status_code=404, detail="Upgrade not found")

    if not upgrade.stripe_price_id:
        raise HTTPException(
            status_code=400,
            detail="This upgrade is not yet configured for payment.",
        )

    # Where Stripe sends the guest after payment
    success_url = str(
        request.url_for("guest_app_ui", property_id=property_id)
    ) + "?upgrade=success"
    cancel_url = str(
        request.url_for("guest_app_ui", property_id=property_id)
    ) + f"?upgrade={upgrade.slug}"

    try:
        checkout_session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[
                {
                    "price": upgrade.stripe_price_id,
                    "quantity": 1,
                }
            ],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "property_id": str(property_id),
                "upgrade_id": str(upgrade.id),
                "upgrade_slug": upgrade.slug or "",
            },
            customer_email=payload.guest_email,
        )
    except Exception as e:
        logging.exception("Stripe checkout creation failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to start checkout for this upgrade.",
        )

    return {"checkout_url": checkout_session.url}

    
@app.post("/properties/{property_id}/chat")
def property_chat(
    request: Request,
    property_id: int,
    payload: PropertyChatRequest,
    db: Session = Depends(get_db)
):
    now = datetime.utcnow()

    # 🔐 optional: enforce unlock server-side
    verified_flag = request.session.get(f"guest_verified_{property_id}", False)
    if not verified_flag:
        # you can choose how strict you want this message to be
        return {
            "response": (
                "For security, please unlock your stay first with the last 4 digits "
                "of the phone number on your reservation, then try again. 🔐"
            )
        }

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

    # 3️⃣ Attach PMS data (phone_last4 + reservation_id + guest info) for this session
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

    # 🔤 Save preferred language onto session (if set)
    if getattr(payload, "language", None) and payload.language != "auto":
        session.language = payload.language

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
    system_prompt = build_system_prompt(prop, pmc, context, session.language)

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
        print(f"[LLM ERROR in /properties/{property_id}/chat]:", e)
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


def get_today_reservation(db: Session, property_id: int) -> Reservation | None:
    """
    Return the reservation (if any) where today falls between
    arrival_date and departure_date (inclusive) for this property.
    """
    today = datetime.utcnow().date()
    return (
        db.query(Reservation)
        .filter(
            Reservation.property_id == property_id,
            Reservation.arrival_date <= today,
            Reservation.departure_date >= today,
        )
        .order_by(Reservation.arrival_date.asc())
        .first()
    )


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


def build_system_prompt(
    prop: Property,
    pmc,
    context: dict,
    session_language: str | None = None
) -> str:
    """
    Build a property-aware system prompt for Sandy.
    Adds support for session.language (preferred language) while
    keeping your original tone, formatting rules, and property data.
    """

    config = context.get("config", {})
    manual = context.get("manual", "")

    house_rules = config.get("house_rules") or ""
    wifi = config.get("wifi") or {}
    wifi_info = ""
    if isinstance(wifi, dict):
        wifi_info = f"WiFi network: {wifi.get('ssid', '')}, password: {wifi.get('password', '')}"

    emergency_phone = config.get("emergency_phone") or (pmc.main_contact if pmc else "")

    # 🔤 Normalize session_language
    # - None or "" or "auto" → auto-detect mirror mode
    # - Otherwise → enforce that language
    lang_code = (session_language or "").strip().lower()

    if lang_code in ("", "auto", None):
        language_instruction = "Always answer in the SAME language the guest uses."
        lang_label = "auto"
    else:
        # enforce a specific language, e.g. 'es' or 'fr'
        lang_label = lang_code
        language_instruction = (
            f"Always answer in **{lang_code.upper()}**, unless the guest clearly switches languages."
        )

    return f"""
        You are Sandy, a beachy, upbeat AI concierge for a vacation rental called "{prop.property_name}".
        
        Property host/manager: {pmc.pmc_name if pmc else "Unknown PMC"}.
        Emergency or urgent issues should be directed to: {emergency_phone} (phone).
        
        Guest preferred language setting: **{lang_label}**  
        {language_instruction}
        
        Always:
        - Use a clear, friendly, warm tone with light emojis.
        - Use markdown formatting: **bold headers**, bullet points, and line breaks.
        - Keep replies concise but helpful.
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



# --- Start Server ---

if __name__ == "__main__":
    try:
        uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
    except Exception as e:
        print(f"Error: {e}")
