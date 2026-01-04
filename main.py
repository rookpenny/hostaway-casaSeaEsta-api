# ---- imports ----
import os
import json
import time
import logging
import requests
import uvicorn
import re
import stripe
import asyncio
import time as pytime
import unicodedata

from pathlib import Path as FSPath

from typing import Optional, Any, List, Dict
from datetime import datetime, timedelta, date, time as dt_time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fastapi import (
    FastAPI, Request, Query, HTTPException, Header, Form,
    APIRouter, Depends, status
)
from fastapi import Path as FPath

from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from seed_guides_route import router as seed_guides_router



from starlette.middleware.sessions import SessionMiddleware
from database import SessionLocal, engine, get_db
from models import Property, ChatSession, ChatMessage, PMC, PMCIntegration, Upgrade, Reservation, Guide

from utils.message_helpers import classify_category, smart_response, detect_log_types
from utils.pms_sync import sync_properties, sync_all_integrations
from utils.pms_access import get_pms_access_info, ensure_pms_data
from utils.prearrival import prearrival_router
from utils.prearrival_debug import prearrival_debug_router
from utils.hostaway import get_upcoming_phone_for_listing, get_listing_overview
from utils.github_sync import ensure_repo

from apscheduler.schedulers.background import BackgroundScheduler

from openai import OpenAI, RateLimitError, AuthenticationError, APIStatusError

from routes import admin, pmc_auth, pmc_signup, stripe_webhook, pmc_onboarding


logger = logging.getLogger("uvicorn.error")
DATA_REPO_DIR = (os.getenv("DATA_REPO_DIR") or "").strip()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Init ---

app = FastAPI()

# --- Routers ---
app.include_router(admin.router)
app.include_router(pmc_auth.router)
app.include_router(prearrival_router)
app.include_router(prearrival_debug_router)
app.include_router(seed_guides_router)
app.include_router(pmc_signup.router)
app.include_router(stripe_webhook.router)
app.include_router(pmc_onboarding.router)
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Seed upgrade  ---
from seed_upgrades_route import router as seed_upgrades_router
app.include_router(seed_upgrades_router)


# Middleware

ALLOWED_ORIGINS = [
    "https://hostaway-casaseaesta-api.onrender.com",
]

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET") or "fallbacksecret",
    same_site="none",
    https_only=True,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static + Templates
templates = Jinja2Templates(directory="templates")

TMP_MAX_AGE_SECONDS = 60 * 60 * 6  # 6 hours
TMP_DIR = FSPath("static/uploads/upgrades/tmp")
TMP_DIR.mkdir(parents=True, exist_ok=True)


async def cleanup_tmp_upgrades_forever():
    while True:
        now = pytime.time()
        for p in TMP_DIR.glob("*"):
            try:
                if p.is_file():
                    age = now - p.stat().st_mtime
                    if age > TMP_MAX_AGE_SECONDS:
                        p.unlink()
            except Exception:
                pass
        await asyncio.sleep(60 * 30)  # every 30 minutes

@app.on_event("startup")
async def _start_cleanup_task():
    asyncio.create_task(cleanup_tmp_upgrades_forever())




@app.on_event("startup")
def ensure_data_repo_on_boot():
    try:
        ensure_repo()
    except Exception:
        logging.getLogger("uvicorn.error").exception("ensure_repo failed (continuing)")


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
    scheduler.add_job(sync_all_integrations, "interval", hours=24)
    scheduler.start()

_scheduler_started = False

@app.on_event("startup")
def _start_scheduler_once():
    global _scheduler_started
    if _scheduler_started:
        return
    start_scheduler()
    _scheduler_started = True


# --- DB Connection Test ---
try:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        print("✅ Database connected successfully.")
except SQLAlchemyError as e:
    print(f"❌ Database connection failed: {e}")



# --- Sync Trigger ---
@app.post("/admin/sync-properties")
def manual_sync(request: Request):
    if request.session.get("role") != "super":
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        count = sync_all_integrations()
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


@app.get("/debug/session")
def debug_session(request: Request):
    return {
        "has_session": True,
        "cookies_present": bool(request.headers.get("cookie")),
        "last_property": request.session.get("last_property"),
        "verified_flags": {k: v for k, v in request.session.items() if str(k).startswith("guest_verified_")},
    }


# --- Root Health Check ---
@app.get("/")
def root():
    return {"message": "Welcome to the multi-property Sandy API (FastAPI edition)!"}


@app.head("/")
def head_root():
    return Response(status_code=200)


@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/routes")
def list_routes():
    return [{"path": route.path, "methods": list(route.methods)} for route in app.router.routes]

# Additional routes (e.g., /properties, /guests, /guest-message, etc.)
# are handled and correct as provided in your current file



@app.get("/properties/{property_id}/guides")
def list_property_guides(
    property_id: int,
    db: Session = Depends(get_db),
):
    # Make sure the property exists
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    # Fetch active guides for this property
    guides = (
        db.query(Guide)
        .filter(
            Guide.property_id == property_id,
            Guide.is_active == True,
        )
        .order_by(Guide.sort_order.asc(), Guide.id.asc())
        .all()
    )

    # Shape response to match front-end expectations
    payload = []
    for g in guides:
        payload.append(
            {
                "id": g.id,
                "property_id": g.property_id,
                "title": g.title,
                "short_description": g.short_description,
                "long_description": g.long_description,
                "body_html": g.body_html,
                "category": g.category,
                "image_url": g.image_url,
                "sort_order": g.sort_order,
            }
        )

    return {"guides": payload}








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


def get_integration_for_property(db: Session, prop: Property) -> PMCIntegration:
    integration_id = getattr(prop, "integration_id", None)
    if not integration_id:
        raise HTTPException(status_code=400, detail="Property is missing integration_id")

    integ = (
        db.query(PMCIntegration)
        .filter(PMCIntegration.id == int(integration_id), PMCIntegration.pmc_id == int(prop.pmc_id))
        .first()
    )
    if not integ:
        raise HTTPException(status_code=400, detail="Integration not found for property")

    return integ

def hour_to_ampm(hour):
    if hour is None:
        return None
    try:
        hour = int(hour)
    except Exception:
        return None

    hour = hour % 24
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12
    if hour12 == 0:
        hour12 = 12

    return f"{hour12}:00 {suffix}"



@app.get("/guest/{property_id}", response_class=HTMLResponse)
def guest_app_ui(request: Request, property_id: int, db: Session = Depends(get_db)):
    # store the property ID so logout can redirect correctly
    request.session["last_property"] = property_id

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    # ✅ HARD GATE: property must have sandy_enabled
    if not bool(getattr(prop, "sandy_enabled", False)):
        return HTMLResponse(
            """
            <html>
              <head>
                <meta name="viewport" content="width=device-width, initial-scale=1" />
                <title>Guest experience unavailable</title>
                <script src="https://cdn.tailwindcss.com"></script>
              </head>
              <body class="min-h-screen bg-[#f5f5f5] flex items-center justify-center p-6">
                <div class="max-w-md w-full bg-white rounded-3xl p-6 shadow-sm text-center">
                  <h1 class="text-2xl font-semibold text-slate-900">Guest experience unavailable</h1>
                  <p class="mt-3 text-slate-600">
                    This property hasn’t enabled Sandy yet. Please contact your host for assistance.
                  </p>
                </div>
              </body>
            </html>
            """,
            status_code=403,
        )

    pmc = getattr(prop, "pmc", None)

    # ✅ define these BEFORE you use them
    prop_provider = (getattr(prop, "provider", None) or getattr(prop, "pms_integration", None) or "").strip().lower()
    pmc_provider = (getattr(pmc, "pms_integration", None) or "").strip().lower() if pmc else ""

    
    
    is_live = bool(getattr(prop, "sandy_enabled", False) and pmc and getattr(pmc, "active", False))


    # ---- Load config/manual from disk ----
    context = load_property_context(prop, db)
    cfg = (context.get("config") or {}) if isinstance(context, dict) else {}
    wifi = cfg.get("wifi") or {}
    
    # ✅ NEW: assistant/personality config (always a dict)
    assistant_config = cfg.get("assistant") if isinstance(cfg.get("assistant"), dict) else {}


    address = cfg.get("address")
    city_name = cfg.get("city_name")
    hero_image_url = cfg.get("hero_image_url")
    experiences_hero_url = cfg.get("experiences_hero_url")

    # ---- Hostaway overrides (if configured) ----
    # Conditions:
    # - PMC provider is hostaway
    # - Property provider is hostaway
    # - Property has pms_property_id
    # - PMC has pms_api_key + pms_api_secret (Hostaway auth)
    if pmc and prop_provider == "hostaway" and getattr(prop, "pms_property_id", None):
        try:
            integ = get_integration_for_property(db, prop)
            provider = (integ.provider or "").strip().lower()
            if provider == "hostaway":
                account_id = (integ.account_id or "").strip()
                api_secret = (integ.api_secret or "").strip()
                if not account_id or not api_secret:
                    raise Exception("Missing Hostaway creds on integration")
    
                hero, ha_address, ha_city = get_listing_overview(
                    listing_id=str(prop.pms_property_id),
                    client_id=account_id,         # Hostaway: account_id
                    client_secret=api_secret,     # Hostaway: api_secret
                )
    
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


    # If no separate experiences hero, reuse main hero
    if not experiences_hero_url and hero_image_url:
        experiences_hero_url = hero_image_url

    # ---- Latest chat session (for basic name/dates) ----
    latest_session = (
        db.query(ChatSession)
        .filter(ChatSession.property_id == prop.id)
        .order_by(ChatSession.last_activity_at.desc())
        .first()
    )

    reservation_name = latest_session.guest_name if latest_session and latest_session.guest_name else None
    arrival_date_db = latest_session.arrival_date if latest_session and latest_session.arrival_date else None
    departure_date_db = latest_session.departure_date if latest_session and latest_session.departure_date else None

    # ---- Today's in-house reservation (for turnover logic) ----
    today_res = get_today_reservation(db, prop.id)  # may be None
    same_day_turnover = compute_same_day_turnover(db, prop.id, today_res)

    # ---- Load upgrades from DB ----
    upgrades = (
        db.query(Upgrade)
        .filter(
            Upgrade.property_id == prop.id,
            Upgrade.is_active.is_(True),
        )
        .order_by(Upgrade.sort_order.asc(), Upgrade.id.asc())
        .all()
    )

    # ---- Filter & shape upgrades for template ----
    visible_upgrades = []
    for up in upgrades:
        slug = (up.slug or "").lower()
        title_lower = (up.title or "").lower() if up.title else ""

        # early/late "time flexibility" upgrade?
        is_time_flex = (
            slug in {"early-check-in", "late-checkout", "late-check-out"}
            or "early check" in title_lower
            or "late check" in title_lower
        )

        # On same-day turnover, hide early check-in / late checkout
        if same_day_turnover and is_time_flex:
            continue

        # Format price for display
        price_display = None
        if up.price_cents is not None:
            currency = (up.currency or "usd").lower()
            amount = up.price_cents / 100.0
            if currency == "usd":
                price_display = f"${amount:,.0f}"
            else:
                price_display = f"{amount:,.2f} {currency.upper()}"

        visible_upgrades.append(
            {
                "id": up.id,
                "slug": up.slug,
                "title": up.title,
                "short_description": up.short_description,
                "long_description": up.long_description,
                "price_cents": up.price_cents,
                "price_currency": up.currency or "usd",
                "price_display": price_display,
                "stripe_price_id": up.stripe_price_id,
                "image_url": getattr(up, "image_url", None),
                "badge": getattr(up, "badge", None),
            }
        )

    # ---- Google Maps link ----
    from urllib.parse import quote_plus

    google_maps_link = None
    if address or city_name:
        q = " ".join(filter(None, [address, city_name]))
        google_maps_link = f"https://www.google.com/maps/search/?api=1&query={quote_plus(q)}"

    checkin_time_display = hour_to_ampm(cfg.get("checkInTimeStart") or cfg.get("checkinTimeStart"))
    #checkout_time_display = hour_to_ampm(cfg.get("checkOutTime") or cfg.get("checkOutTime"))
    checkout_time_display = hour_to_ampm(cfg.get("checkOutTime") or cfg.get("checkoutTime") or cfg.get("checkOutTimeEnd"))



    # ---- Render template ----
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
            "checkin_time": checkin_time_display,
            "checkout_time": checkout_time_display,

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
            "sandy_enabled": bool(getattr(prop, "sandy_enabled", False)),
            "is_verified": request.session.get(f"guest_verified_{property_id}", False),

            # ✅ NEW: assistant/personality config for guest_app.html
            "assistant_config": assistant_config,

            # Upgrades + turnover flags for the template
            "upgrades": visible_upgrades,
            "same_day_turnover": same_day_turnover,
            "hide_time_flex": same_day_turnover,
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

'''
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


'''

class VerifyRequest(BaseModel):
    code: str


def _parse_ymd(d: Optional[str]) -> Optional[datetime.date]:
    if not d:
        return None
    s = str(d).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _format_time_display(value: Any, default: str = "") -> str:
    if value is None:
        return default

    if isinstance(value, datetime):
        return value.strftime("%-I:%M %p")
    if isinstance(value, dt_time):
        return value.strftime("%-I:%M %p")

    s = str(value).strip()
    if not s:
        return default

    try:
        dt = datetime.strptime(s, "%H:%M")
        return dt.strftime("%-I:%M %p")
    except Exception:
        pass

    try:
        dt = datetime.strptime(s.upper(), "%I:%M %p")
        return dt.strftime("%-I:%M %p")
    except Exception:
        pass

    return s



@app.post("/guest/{property_id}/verify-json")
def verify_json(
    property_id: int,
    payload: VerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    code = (payload.code or "").strip()

    # 1) format check
    if not code.isdigit() or len(code) != 4:
        return JSONResponse(
            {"success": False, "error": "Please enter exactly 4 digits."},
            status_code=400,
        )

    # 2) test override
    test_code = (os.getenv("TEST_UNLOCK_CODE") or "").strip()
    if test_code and code == test_code:
        request.session[f"guest_verified_{property_id}"] = True
        today = datetime.utcnow().date()
        return {
            "success": True,
            "guest_name": "Test Guest",
            "arrival_date": today.strftime("%Y-%m-%d"),
            "departure_date": (today + timedelta(days=3)).strftime("%Y-%m-%d"),
            "checkin_time": "4:00 PM",
            "checkout_time": "10:00 AM",
        }

    # 3) load property + pmc
    prop = db.query(Property).filter(Property.id == int(property_id)).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    pmc = getattr(prop, "pmc", None)
    if not pmc:
        return JSONResponse(
            {"success": False, "error": "This property is not linked to a PMC."},
            status_code=400,
        )

    # 4) integration/provider resolution (safe)
    integ = None
    try:
        integ = get_integration_for_property(db, prop)
    except Exception:
        integ = None

    prop_provider = (getattr(prop, "provider", None) or "").strip().lower()
    integ_provider = (getattr(integ, "provider", None) or "").strip().lower()
    provider = prop_provider or integ_provider

    # 5) fetch last4 + guest info
    try:
        if provider == "hostaway" and getattr(prop, "pms_property_id", None):
            account_id = (getattr(integ, "account_id", None) or "").strip()
            api_secret = (getattr(integ, "api_secret", None) or "").strip()

            if not account_id or not api_secret:
                raise Exception("Missing Hostaway creds on integration (account_id/api_secret)")

            (
                phone_last4,
                _door_code,       # unused; kept for consistency
                reservation_id,
                guest_name,
                arrival_date,
                departure_date,
            ) = get_upcoming_phone_for_listing(
                listing_id=str(prop.pms_property_id),
                client_id=account_id,
                client_secret=api_secret,
            )
        else:
            (
                phone_last4,
                _door_code,       # unused; kept for consistency
                reservation_id,
                guest_name,
                arrival_date,
                departure_date,
            ) = get_pms_access_info(pmc, prop)

    except Exception as e:
        print("[VERIFY PMS ERROR]", repr(e))
        return JSONResponse(
            {"success": False, "error": "Could not verify your reservation. Please try again."},
            status_code=500,
        )

    # 6) 30-day arrival window
    WINDOW_DAYS = 30
    today = datetime.utcnow().date()
    arrival_obj = _parse_ymd(arrival_date)

    if arrival_obj and arrival_obj > today + timedelta(days=WINDOW_DAYS):
        return JSONResponse(
            {"success": False, "error": f"You can only unlock this stay within {WINDOW_DAYS} days of arrival."},
            status_code=400,
        )

    # 7) require reservation + phone
    if not phone_last4 or not reservation_id:
        return JSONResponse(
            {"success": False, "error": "No upcoming reservation found for this property."},
            status_code=400,
        )

    # Normalize just in case Hostaway returns " 5366"
    phone_last4 = str(phone_last4).strip()
    if code != phone_last4:
        return JSONResponse(
            {"success": False, "error": "That code does not match the reservation phone number."},
            status_code=403,
        )

    # 8) compute display times safely (avoid NameError)
    # If you have config-driven times, replace these defaults with your cfg values.
    checkin_time_display = _format_time_display(getattr(prop, "checkin_time", None), default="4:00 PM")
    checkout_time_display = _format_time_display(getattr(prop, "checkout_time", None), default="10:00 AM")

    # 9) success
    request.session[f"guest_verified_{property_id}"] = True
    return {
        "success": True,
        "guest_name": guest_name or "Guest",
        "arrival_date": arrival_date,
        "departure_date": departure_date,
        "checkin_time": checkin_time_display,
        "checkout_time": checkout_time_display,
        "reservation_id": reservation_id,  # optional but often useful
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

    
# Use uvicorn logger (shows in Render runtime logs more reliably than print)
logger = logging.getLogger("uvicorn.error")


# ----------------------------
# Link normalization utilities
# ----------------------------
_URL_RE = re.compile(r'(https?://[^\s\)\]"\']+|www\.[^\s\)\]"\']+)', re.IGNORECASE)
_MD_LINK_RE = re.compile(r'\[([^\]]+)\]\((.*?)\)', re.DOTALL)

def _normalize_url(url: str) -> str:
    if not url:
        return ""
    u = url.strip()

    # Trim common trailing punctuation that gets stuck to URLs
    while u and u[-1] in ".,);:!?]":
        u = u[:-1]

    if u.lower().startswith("www."):
        u = "https://" + u
    return u

def _extract_first_url(s: str) -> str:
    if not s:
        return ""
    m = _URL_RE.search(s)
    if not m:
        return ""
    return _normalize_url(m.group(1))

def enforce_click_here_links(text: str) -> str:
    """
    Normalizes any link-ish output into ONE consistent markdown format:

      [Click here for directions](URL)

    Rules:
    - Remove any HTML (<a>, etc.)
    - Convert ANY markdown link label -> "Click here for directions"
    - Hide ANY raw URL behind the same markdown anchor
    - Avoid nested links
    """
    if not text:
        return text

    out = text

    # 1) Convert HTML anchors -> markdown (and strip any other tags)
    def _html_anchor_repl(m: re.Match) -> str:
        url = _normalize_url(m.group(1) or "")
        return f"[Click here for directions]({url})" if url else "Click here for directions"

    out = re.sub(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>.*?</a>',
        _html_anchor_repl,
        out,
        flags=re.IGNORECASE | re.DOTALL,
    )
    out = re.sub(r"<[^>]+>", "", out)

    # 2) Normalize ALL markdown links -> strict anchor, extract first URL inside ()
    def _md_link_repl(m: re.Match) -> str:
        raw_target = (m.group(2) or "").strip()
        url = _extract_first_url(raw_target)
        return f"[Click here for directions]({url})" if url else (m.group(1) or "")

    out = _MD_LINK_RE.sub(_md_link_repl, out)

    # 3) Replace any remaining raw URLs -> strict anchor
    def _raw_url_repl(m: re.Match) -> str:
        url = _normalize_url(m.group(1) or "")
        return f"[Click here for directions]({url})" if url else ""

    out = _URL_RE.sub(_raw_url_repl, out)

    # 4) Collapse repeated identical anchors (optional cleanup)
    out = re.sub(
        r'(\[Click here for directions\]\([^)]+\))(\s+\1)+',
        r'\1',
        out,
        flags=re.IGNORECASE,
    )

    return out


@app.post("/properties/{property_id}/chat")
async def property_chat(
    request: Request,
    property_id: int,
    payload: PropertyChatRequest,
    db: Session = Depends(get_db),
):
    now = datetime.utcnow()

    # enable streaming with ?stream=1
    stream_mode = request.query_params.get("stream") == "1"

    # 0) TEMP / SAFE DEBUG MODE (no logs required)
    debug_mode = request.query_params.get("debug") == "1"

    verified_key = f"guest_verified_{property_id}"
    verified_flag = bool(request.session.get(verified_key, False))

    if debug_mode:
        return {
            "debug": {
                "cookie_header_present": bool(request.headers.get("cookie")),
                "cookie_header": request.headers.get("cookie"),
                "session_keys": list(request.session.keys()),
                "verified_key": verified_key,
                "verified_value": verified_flag,
            },
            "response": "debug mode",
        }

    # 1) Enforce unlock before allowing chat
    if not verified_flag:
        return {
            "response": (
                "For security, please unlock your stay first with the last 4 digits "
                "of the phone number on your reservation, then try again. 🔐"
            )
        }

    # 2) Validate message
    user_message = (payload.message or "").strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Message is required")

    lowered = user_message.lower()

    # 3) Look up property + PMC and enforce Sandy enabled
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    pmc = db.query(PMC).filter(PMC.id == prop.pmc_id).first()
    sandy_enabled = bool(getattr(prop, "sandy_enabled", False))
    pmc_active = bool(pmc and getattr(pmc, "active", False))

    if not sandy_enabled or not pmc_active:
        return {
            "response": (
                "Sandy is currently offline for this property 🌙\n\n"
                "Please contact your host directly for assistance."
            )
        }

    # 4) Create or reuse a ChatSession
    session: Optional[ChatSession] = None

    if payload.session_id:
        session = (
            db.query(ChatSession)
            .filter(ChatSession.id == payload.session_id, ChatSession.property_id == property_id)
            .first()
        )

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

    # 5) Attach PMS data
    try:
        ensure_pms_data(db, session)
    except Exception:
        logger.exception("ensure_pms_data failed (continuing)")

    # 6) Log guest message
    category = classify_category(user_message)
    log_type = detect_log_types(user_message)
    sentiment = simple_sentiment(user_message)

    db.add(ChatMessage(
        session_id=session.id,
        sender="guest",
        content=user_message,
        category=category,
        log_type=log_type,
        sentiment=sentiment,
        created_at=now,
    ))
    session.last_activity_at = now

    # Save preferred language
    if getattr(payload, "language", None) and payload.language != "auto":
        session.language = payload.language

    db.commit()
    db.refresh(session)

    # 7) Door code branch (keep your logic)
    code_keywords = ["door code", "access code", "entry code", "pin", "key code", "lock code"]
    is_code_request = any(k in lowered for k in code_keywords)

    code_match = re.search(r"\b(\d{4})\b", user_message)
    provided_last4 = code_match.group(1) if code_match else None
    pms_last4 = getattr(session, "phone_last4", None)

    if is_code_request:
        if pms_last4 and provided_last4 and provided_last4 == str(pms_last4).strip():
            door_code = str(pms_last4).strip()
            reply_text = (
                f"**Your door code** 🔐\n\n"
                f"- Entry code: **{door_code}**\n"
                f"- This matches the last 4 digits of the phone number on your reservation.\n\n"
                "If the lock gives any trouble, try the code slowly and firmly, "
                "and contact your host if it still doesn’t work."
            )
        elif pms_last4 and not provided_last4:
            reply_text = (
                "I can help with your door code 🔐\n\n"
                "For security, please reply with the **last 4 digits of the phone number** "
                "on your reservation, and I’ll confirm your entry code."
            )
        else:
            reply_text = (
                "I’m not seeing an active reservation linked to this chat yet, "
                "so I can’t safely share an access code. 😕\n\n"
                "Please double-check that you’re using the phone number on the booking, "
                "or contact your host directly for access help."
            )

        db.add(ChatMessage(
            session_id=session.id,
            sender="assistant",
            content=reply_text,
            created_at=datetime.utcnow(),
        ))
        db.commit()

        # Door-code can stay non-streaming (simple + instant)
        return {"response": reply_text, "session_id": session.id}

    # 8) Build system prompt + history
    context = load_property_context(prop, db)
    system_prompt = build_system_prompt(
        prop=prop,
        pmc=pmc,
        context=context,
        session_language=session.language,
        session=session,
    )

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

    # --------------------------
    # STREAMING MODE (?stream=1)
    # --------------------------
    if stream_mode:

        async def ndjson_stream():
            full_parts: list[str] = []

            try:
                stream = client.chat.completions.create(
                    model="gpt-4o-mini",
                    temperature=0.7,
                    messages=messages,
                    stream=True,
                )

                for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if not delta:
                        continue
                    full_parts.append(delta)
                    yield (json.dumps({"delta": delta}) + "\n").encode("utf-8")

            except Exception as e:
                yield (json.dumps({"error": str(e)}) + "\n").encode("utf-8")
                return

            reply_text = "".join(full_parts).strip()
            reply_text = enforce_click_here_links(reply_text)

            db.add(ChatMessage(
                session_id=session.id,
                sender="assistant",
                content=reply_text,
                created_at=datetime.utcnow(),
            ))
            db.commit()

            yield (json.dumps({"done": True, "session_id": session.id}) + "\n").encode("utf-8")

        return StreamingResponse(
            ndjson_stream(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --------------------------
    # NON-STREAM MODE (default)
    # --------------------------
    try:
        ai_response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.7,
            messages=messages,
        )
        reply_text = ai_response.choices[0].message.content or ""
        reply_text = enforce_click_here_links(reply_text)
    except RateLimitError:
        logger.exception("OpenAI rate/quota limit")
        reply_text = (
            "I’m temporarily unavailable because we hit our AI usage limit. 🐚\n\n"
            "Please try again in a little bit, or contact your host if it’s urgent."
        )
    except AuthenticationError:
        logger.exception("OpenAI auth error")
        reply_text = (
            "I’m temporarily unavailable due to a configuration issue. 🐚\n\n"
            "Please contact your host for urgent help."
        )
    except APIStatusError:
        logger.exception("OpenAI API status error")
        reply_text = (
            "I’m having trouble connecting right now. 🐚\n\n"
            "Please try again in a moment."
        )
    except Exception:
        logger.exception("OpenAI unknown error")
        reply_text = (
            "Oops, I ran into a technical issue while answering just now. 🐚\n\n"
            "Please try again in a moment, or contact your host directly if it’s urgent."
        )

    db.add(ChatMessage(
        session_id=session.id,
        sender="assistant",
        content=reply_text,
        created_at=datetime.utcnow(),
    ))
    db.commit()

    return {"response": reply_text, "session_id": session.id}



@app.get("/debug/property-context/{property_id}")
def debug_property_context(property_id: int, db: Session = Depends(get_db)):
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    ctx = load_property_context(prop, db)
    return {
        "property_id": prop.id,
        "base_dir": ctx.get("base_dir"),
        "has_config": bool(ctx.get("config")),
        "manual_len": len((ctx.get("manual") or "").strip()),
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
    Return the *current* reservation if today falls between arrival_date and
    departure_date (inclusive). If there is no current stay, fall back to the
    *next upcoming* reservation for this property.
    """
    today = datetime.utcnow().date()

    # 1) Try in-house (today within the stay)
    current = (
        db.query(Reservation)
        .filter(
            Reservation.property_id == property_id,
            Reservation.arrival_date <= today,
            Reservation.departure_date >= today,
        )
        .order_by(Reservation.arrival_date.asc())
        .first()
    )
    if current:
        return current

    # 2) Fallback: next upcoming reservation
    upcoming = (
        db.query(Reservation)
        .filter(
            Reservation.property_id == property_id,
            Reservation.arrival_date >= today,
        )
        .order_by(Reservation.arrival_date.asc())
        .first()
    )
    return upcoming



# Matches any URL-ish string (including goo.gl, maps links, etc.)
_URL_RE = re.compile(r'(https?://[^\s\)"]+|www\.[^\s\)"]+)', re.IGNORECASE)

# Matches markdown links: [text](url)
_MD_LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')




def load_property_context(prop: "Property", db) -> dict:
    """
    Loads config/manual for a property.

    Resolution order:
      1) prop.data_folder_path (absolute OR relative to DATA_REPO_DIR)
      2) {DATA_REPO_DIR}/data/{provider}_{account_id}/{provider}_{pms_property_id}/
      3) {DATA_REPO_DIR}/defaults/
    """

    def _read_json(path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.warning("load_property_context: failed json %s: %r", path, e)
            return {}

    def _read_text(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""
        except Exception as e:
            logger.warning("load_property_context: failed text %s: %r", path, e)
            return ""

    def _slugify(value: str, max_length: int = 128) -> str:
        if not value:
            return "unknown"
        value = unicodedata.normalize("NFKD", value)
        value = value.encode("ascii", "ignore").decode("ascii")
        value = value.lower()
        value = re.sub(r"[^\w\-]+", "_", value)
        value = re.sub(r"_+", "_", value).strip("_")
        return value[:max_length]

    def _abs_in_repo(path: str) -> str:
        """
        If given a repo-relative path like 'data/hostaway_63652/hostaway_256853',
        convert to absolute inside DATA_REPO_DIR.
        """
        p = (path or "").strip()
        if not p:
            return ""
        if os.path.isabs(p):
            return p
        if not DATA_REPO_DIR:
            return p  # can't resolve; return as-is
        return os.path.join(DATA_REPO_DIR, p)

    used_default_cfg = False
    used_default_manual = False
    resolved_from = "none"

    # -------------------------
    # 1) explicit override
    # -------------------------
    base_dir = _abs_in_repo(getattr(prop, "data_folder_path", None) or "")
    if base_dir:
        resolved_from = "prop.data_folder_path"

    # -------------------------
    # 2) computed from provider+account_id+pms_property_id
    # -------------------------
    if (not base_dir) and DATA_REPO_DIR:
        provider = (getattr(prop, "provider", None) or "").strip().lower()
        pms_property_id = getattr(prop, "pms_property_id", None)
        pms_property_id = str(pms_property_id).strip() if pms_property_id is not None else ""

        account_id = ""
        try:
            integ = get_integration_for_property(db, prop)
            account_id = (getattr(integ, "account_id", None) or "").strip()
        except Exception as e:
            logger.warning(
                "load_property_context: could not resolve integration/account_id for prop_id=%s: %r",
                getattr(prop, "id", None),
                e,
            )

        if provider and account_id and pms_property_id:
            acct_dir = f"{provider}_{_slugify(account_id)}"
            prop_dir = f"{provider}_{_slugify(pms_property_id)}"

            #base_dir = os.path.join(DATA_REPO_DIR, "data", acct_dir, prop_dir)
            base_dir = os.path.join(
                DATA_REPO_DIR,
                "data",
                f"{provider}_{account_id}",
                f"{provider}_{pms_property_id}",
            )
            resolved_from = "computed(provider+account_id+pms_property_id)"

    config: Dict[str, Any] = {}
    manual_text: str = ""

    # -------------------------
    # Read property-specific files
    # -------------------------
    if base_dir:
        cfg_path = os.path.join(base_dir, "config.json")
        man_path = os.path.join(base_dir, "manual.txt")

        if os.path.exists(cfg_path):
            config = _read_json(cfg_path)
        if os.path.exists(man_path):
            manual_text = _read_text(man_path)

    # -------------------------
    # 3) defaults fallback
    # -------------------------
    if DATA_REPO_DIR:
        defaults_dir = os.path.join(DATA_REPO_DIR, "defaults")
        fallback_cfg = os.path.join(defaults_dir, "config.json")
        fallback_man = os.path.join(defaults_dir, "manual.txt")

        if not config and os.path.exists(fallback_cfg):
            config = _read_json(fallback_cfg)
            used_default_cfg = True

        if not manual_text.strip() and os.path.exists(fallback_man):
            manual_text = _read_text(fallback_man)
            used_default_manual = True

    logger.info(
        "context: prop_id=%s provider=%s pms_property_id=%s base_dir=%s resolved_from=%s default_cfg=%s default_manual=%s manual_len=%s",
        getattr(prop, "id", None),
        (getattr(prop, "provider", None) or "").strip().lower(),
        getattr(prop, "pms_property_id", None),
        base_dir,
        resolved_from,
        used_default_cfg,
        used_default_manual,
        len((manual_text or "").strip()),
    )

    return {"config": config, "manual": manual_text, "base_dir": base_dir}


def build_system_prompt(
    prop: Property,
    pmc,
    context: dict,
    session_language: str | None = None,
    session: ChatSession | None = None,
) -> str:
    """
    Build a property-aware system prompt for Sandy.
    Includes (optional) verified guest stay context.
    Forces link formatting that your UI can render reliably.
    """

    config = context.get("config", {}) or {}
    manual = context.get("manual", "") or ""

    # assistant/personality config
    assistant = config.get("assistant") if isinstance(config.get("assistant"), dict) else {}
    assistant_name = (assistant.get("name") or "Sandy").strip()
    assistant_style = (assistant.get("style") or "").strip()
    assistant_do = assistant.get("do") if isinstance(assistant.get("do"), list) else []
    assistant_dont = assistant.get("dont") if isinstance(assistant.get("dont"), list) else []

    house_rules = config.get("house_rules") or ""
    wifi = config.get("wifi") or {}

    wifi_info = ""
    if isinstance(wifi, dict):
        ssid = (wifi.get("ssid") or "").strip()
        pw = (wifi.get("password") or "").strip()
        if ssid or pw:
            wifi_info = f"WiFi network: {ssid}, password: {pw}"

    emergency_phone = config.get("emergency_phone") or (getattr(pmc, "main_contact", "") if pmc else "")

    # ----------------------------
    # Guest stay details (verified)
    # ----------------------------
    guest_block = ""
    if session:
        guest_name = (getattr(session, "guest_name", None) or "").strip()
        arrival_date = getattr(session, "arrival_date", None)
        departure_date = getattr(session, "departure_date", None)

        if guest_name or arrival_date or departure_date:
            guest_block = f"""
            Verified guest stay details (PRIVATE):
            - Guest name: {guest_name or "Unknown"}
            - Check-in date: {arrival_date or "Unknown"}
            - Check-out date: {departure_date or "Unknown"}
            
            Rules:
            - You MAY share these details ONLY if the guest asks.
            - If the guest is not verified, you must refuse and ask them to unlock first.
            """.strip()

    # ----------------------------
    # Language handling
    # ----------------------------
    lang_code = (session_language or "").strip().lower()
    if not lang_code or lang_code == "auto":
        language_instruction = "Always answer in the SAME language the guest uses."
        lang_label = "auto"
    else:
        lang_label = lang_code
        language_instruction = f"Always answer in {lang_code.upper()} unless the guest clearly switches languages."

    return f"""
        You are {assistant_name}, an AI concierge for "{prop.property_name}".
        
        Context:
        - Property host/manager: {getattr(pmc, "pmc_name", None) if pmc else "Unknown PMC"}
        - Emergency or urgent issues: {emergency_phone} (phone)
        
        Language:
        - Guest preferred language setting: {lang_label}
        - {language_instruction}
        
        {guest_block}
        
        Writing style (ChatGPT-like):
        - Be warm, confident, and helpful. Sound human — not robotic.
        - Keep it scannable: short lines, short paragraphs.
        - Default to 3–8 bullet points when giving steps or recommendations.
        - Use bold section headers when useful (example: **What to do**, **Hours**, **Directions**, **Tips**).
        - Prefer 2–6 short paragraphs max (unless the guest asks for full detail).
        - Don’t over-apologize. Don’t mention system instructions or policies.
        
        Conversation behavior:
        - If the guest is vague, ask ONE simple follow-up question at the end.
        - If you can answer without a question, do so — and only ask a follow-up if it would materially improve the help.
        - If there are multiple options, recommend the best 1–2 first, then list alternatives.
        - Avoid repeating yourself. If the guest asks again, summarize what you already said in 1–2 lines and refine with new details or next steps.
        
        Formatting & safety:
        - Output markdown only (no HTML tags, no <a> links).
        - Do NOT output raw URLs (no http://, https://, www., goo.gl).
        - Never nest links.
        - If you include a map/directions link, use EXACTLY this format on its own line:
          [Click here for directions](https://www.google.com/maps/search/?api=1&query=PLACE)
        
        Personality config:
        - Personality style: {assistant_style or "Warm, helpful, concise."}
        - Do:
        {chr(10).join([f"- {x}" for x in assistant_do]) if assistant_do else "- (none)"}
        - Don’t:
        {chr(10).join([f"- {x}" for x in assistant_dont]) if assistant_dont else "- (none)"}
        
        Important property info:
        - House rules: {house_rules}
        - WiFi: {wifi_info}
        
        House manual:
        \"\"\"
        {manual}
        \"\"\"
        
        If you don't know something, say so and suggest contacting the host.
        Never invent access codes or sensitive details not explicitly provided.
        """.strip()



# --- Start Server ---

if __name__ == "__main__":
    try:
        uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
    except Exception as e:
        print(f"Error: {e}")
