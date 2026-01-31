# ---- imports ----
import os
import json
import logging
import re
import asyncio
import time as pytime
import unicodedata
import traceback

import stripe
import uvicorn

from utils.hostaway import (
    get_upcoming_phone_for_listing,  # (optional now; can remove later)
    get_listing_overview,
    fetch_reservations,
    get_token_for_pmc,
)

from sqlalchemy import and_, cast, Date, func

from urllib.parse import quote_plus

from pathlib import Path as FSPath
from typing import Optional, Any, Dict, Literal, TypedDict
from datetime import datetime, timedelta, time as dt_time

from sqlalchemy import text, desc
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.inspection import inspect as sa_inspect

from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.responses import JSONResponse, HTMLResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles


from pydantic import BaseModel

from apscheduler.schedulers.background import BackgroundScheduler
from openai import OpenAI, RateLimitError, AuthenticationError, APIStatusError

from database import engine, get_db
from models import Property, ChatSession, ChatMessage, PMC, PMCIntegration, Upgrade, Reservation, Guide

from routes.analytics import router as analytics_router
from routes.admin_analytics_ui import router as admin_analytics_ui_router
from routes.admin_analytics import router as admin_analytics_api_router
from routes.upgrade_purchase_status import router as upgrade_purchase_status_router

from routes.stripe_connect import router as stripe_connect_router
from routes.upgrade_checkout import router as upgrade_checkout_router




from routes.reports import router as reports_router

from routes import admin, pmc_auth, pmc_signup, stripe_webhook, pmc_onboarding
from seed_guides_route import router as seed_guides_router
from seed_upgrades_route import router as seed_upgrades_router


from starlette.middleware.sessions import SessionMiddleware

from utils.message_helpers import classify_category, detect_log_types
from utils.pms_sync import sync_all_integrations
from utils.pms_access import get_pms_access_info, ensure_pms_data
from utils.prearrival import prearrival_router
from utils.prearrival_debug import prearrival_debug_router

from utils.github_sync import ensure_repo
from utils.ai_summary import maybe_autosummarize_on_new_guest_message
from utils.sentiment import classify_guest_sentiment



logger = logging.getLogger("uvicorn.error")
DATA_REPO_DIR = (os.getenv("DATA_REPO_DIR") or "").strip()

app = FastAPI()
WINDOW_DAYS = int(os.getenv("WINDOW_DAYS", "120"))



# --- Routers ---
app.include_router(analytics_router)
app.include_router(admin_analytics_ui_router)
app.include_router(admin_analytics_api_router)

app.include_router(admin.router)
app.include_router(pmc_auth.router)
app.include_router(prearrival_router)
app.include_router(prearrival_debug_router)
app.include_router(seed_guides_router)
app.include_router(seed_upgrades_router)
app.include_router(pmc_signup.router)
app.include_router(stripe_webhook.router)
app.include_router(pmc_onboarding.router)
app.include_router(stripe_connect_router)
app.include_router(upgrade_checkout_router)
#app.include_router(upgrade_pages_router)
app.include_router(upgrade_purchase_status_router)
app.include_router(reports_router)

app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Middleware ---
ALLOWED_ORIGINS = [
    "https://hostaway-casaseaesta-api.onrender.com",
    "http://localhost:3000",
    "http://localhost:5173",
]


SESSION_SECRET = (os.getenv("SESSION_SECRET") or "").strip()
if not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET missing")


app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
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

templates = Jinja2Templates(directory="templates")

TMP_MAX_AGE_SECONDS = 60 * 60 * 6  # 6 hours
TMP_DIR = FSPath("static/uploads/upgrades/tmp")
TMP_DIR.mkdir(parents=True, exist_ok=True)


# ----------------------------
# Giving OPENAI full sentiment/ mood control
# ----------------------------

Sentiment = Literal["negative", "neutral", "positive"]
Mood = Literal["angry", "confused", "worried", "upset", "panicked", "stressed", "calm", "other"]

class SentimentResult(TypedDict, total=False):
    sentiment: Sentiment
    mood: Mood
    confidence: int  # 0-100


def normalize_sentiment_label(value) -> str:
    """
    Accepts:
      - "positive"/"neutral"/"negative"
      - {"sentiment": "...", ...}
      - {"label": "...", ...}
    Returns a safe label string.
    """
    if value is None:
        return "neutral"

    # If OpenAI returns a dict/object
    if isinstance(value, dict):
        value = value.get("sentiment") or value.get("label") or value.get("value")

    label = str(value).strip().lower()
    if label not in {"positive", "neutral", "negative"}:
        return "neutral"
    return label




def _is_time_flex_upgrade(up: Upgrade) -> tuple[bool, str]:
    slug = (up.slug or "").lower()
    title_lower = (up.title or "").lower()
    kind = None

    if slug in {"early-check-in"} or "early check" in title_lower:
        kind = "early_checkin"
    elif slug in {"late-checkout", "late-check-out"} or "late check" in title_lower:
        kind = "late_checkout"

    return (kind is not None, kind or "")




@app.get("/guest/properties/{property_id}/upgrades/availability")
def guest_upgrades_availability(
    property_id: int,
    session_id: str | None = None,
    db: Session = Depends(get_db),
):
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    guest_session = (
        db.query(ChatSession)
        .filter(ChatSession.property_id == property_id, ChatSession.id == int(session_id))
        .first()
    )
    if not guest_session:
        raise HTTPException(status_code=404, detail="ChatSession not found for session_id")

    arrival = getattr(guest_session, "arrival_date", None)
    departure = getattr(guest_session, "departure_date", None)
    guest_reservation_id = getattr(guest_session, "reservation_id", None)

    turnover_arrival = turnover_on_arrival_day(db, property_id, arrival, guest_reservation_id)
    turnover_departure = turnover_on_departure_day(db, property_id, departure, guest_reservation_id)

    upgrades = (
        db.query(Upgrade)
        .filter(Upgrade.property_id == property_id, Upgrade.is_active.is_(True))
        .order_by(Upgrade.sort_order.asc(), Upgrade.id.asc())
        .all()
    )

    out = []
    for up in upgrades:
        is_available = True
        reason = ""

        is_time_flex, kind = _is_time_flex_upgrade(up)

        if is_time_flex and kind == "early_checkin" and turnover_arrival:
            is_available = False
            reason = "Not available for same-day turnovers."
        elif is_time_flex and kind == "late_checkout" and turnover_departure:
            is_available = False
            reason = "Not available for same-day turnovers."

        out.append(
            {
                "upgrade_id": up.id,
                "is_available": bool(is_available),
                "unavailable_reason": reason or None,
            }
        )

    return {
        "session_id_used": guest_session.id,
        "arrival_date": str(arrival) if arrival else None,
        "departure_date": str(departure) if departure else None,
        "turnover_on_arrival": turnover_arrival,
        "turnover_on_departure": turnover_departure,
        "items": out,
    }




def classify_sentiment_openai(
    client: OpenAI,
    text: str,
    model: str = "gpt-4o-mini",
) -> Optional[SentimentResult]:
    """
    OpenAI-first classifier.
    Returns dict with sentiment/mood/confidence or None on failure.
    """
    msg = (text or "").strip()
    if not msg:
        return None

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict text classifier.\n"
                        "Return ONLY valid JSON.\n"
                        "Schema:\n"
                        '{ "sentiment": "negative|neutral|positive", '
                        '"mood": "angry|confused|worried|upset|panicked|stressed|calm|other", '
                        '"confidence": 0-100 }\n'
                        "Rules:\n"
                        "- sentiment is overall valence.\n"
                        "- mood is the dominant emotion.\n"
                        "- confidence is your confidence.\n"
                        "- No extra keys. No markdown. No explanation."
                    ),
                },
                {"role": "user", "content": msg},
            ],
            # If your OpenAI SDK/model supports it, this helps force JSON.
            response_format={"type": "json_object"},
        )

        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)

        sentiment = (data.get("sentiment") or "").strip().lower()
        mood = (data.get("mood") or "").strip().lower()
        confidence = data.get("confidence")

        if sentiment not in {"negative", "neutral", "positive"}:
            return None
        if mood not in {"angry","confused","worried","upset","panicked","stressed","calm","other"}:
            mood = "other"

        try:
            confidence = int(confidence)
        except Exception:
            confidence = 0
        confidence = max(0, min(100, confidence))

        return {"sentiment": sentiment, "mood": mood, "confidence": confidence}

    except Exception:
        return None


def classify_sentiment_with_fallback(client: OpenAI, text: str) -> SentimentResult:
    """
    Always returns something usable.
    """
    r = classify_sentiment_openai(client, text)
    if r and r.get("sentiment"):
        return r

    # fallback: your deterministic rules
    s = simple_sentiment(text)  # returns negative/neutral/positive
    return {"sentiment": s, "mood": "other", "confidence": 0}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "path": str(request.url.path),
            "trace": traceback.format_exc().splitlines()[-15:],  # last lines only
        },
    )


# ----------------------------
# Link normalization utilities
# ----------------------------
# Matches any URL-ish string (includes goo.gl, maps links, etc.)
_URL_RE = re.compile(r'(https?://[^\s\)\]"\']+|www\.[^\s\)\]"\']+)', re.IGNORECASE)
# Matches markdown links: [text](url)
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

    # Convert HTML anchors -> markdown (and strip any other tags)
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

    # Normalize ALL markdown links -> strict anchor
    def _md_link_repl(m: re.Match) -> str:
        raw_target = (m.group(2) or "").strip()
        url = _extract_first_url(raw_target)
        return f"[Click here for directions]({url})" if url else (m.group(1) or "")

    out = _MD_LINK_RE.sub(_md_link_repl, out)

    # Replace any remaining raw URLs -> strict anchor
    def _raw_url_repl(m: re.Match) -> str:
        url = _normalize_url(m.group(1) or "")
        return f"[Click here for directions]({url})" if url else ""

    out = _URL_RE.sub(_raw_url_repl, out)

    # Collapse repeated identical anchors
    out = re.sub(
        r'(\[Click here for directions\]\([^)]+\))(\s+\1)+',
        r'\1',
        out,
        flags=re.IGNORECASE,
    )

    return out


# --- OpenAI bootstrap (single source of truth) ---
def init_openai_client(app: FastAPI) -> None:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing or empty")

    try:
        client = OpenAI(api_key=api_key)

        # HARD validation: forces auth header to be tested at boot
        client.models.list()

    except AuthenticationError as e:
        logger.error("❌ OpenAI authentication failed at startup")
        raise RuntimeError("Invalid OPENAI_API_KEY") from e

    except Exception as e:
        logger.exception("❌ OpenAI initialization failed")
        raise RuntimeError("Failed to initialize OpenAI client") from e

    app.state.openai = client
    logger.info("✅ OpenAI client initialized and validated")


def get_openai(req: Request) -> OpenAI:
    client = getattr(req.app.state, "openai", None)
    if client is None:
        raise HTTPException(status_code=500, detail="OpenAI client not initialized")
    return client


@app.on_event("startup")
def startup_openai() -> None:
    # initializes and validates the client
    init_openai_client(app)


@app.get("/debug/openai")
def debug_openai(request: Request):
    return {"openai_initialized": hasattr(request.app.state, "openai")}


# --- background cleanup task ---
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
        await asyncio.sleep(60 * 30)


@app.on_event("startup")
async def _start_cleanup_task():
    asyncio.create_task(cleanup_tmp_upgrades_forever())


# --- Boot jobs ---
@app.on_event("startup")
def ensure_data_repo_on_boot():
    try:
        ensure_repo()
    except Exception:
        logger.exception("ensure_repo failed (continuing)")


# --- Scheduler ---
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


# --- Validation error handler (define ONCE) ---
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error("❌ Validation Error: %s", exc.errors())
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )


# --- DB Connection Test ---
try:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        logger.info("✅ Database connected successfully.")
except SQLAlchemyError as e:
    logger.error("❌ Database connection failed: %r", e)


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
            status_code=500,
        )


@app.get("/debug/session")
def debug_session(request: Request):
    return {
        "has_session": True,
        "cookies_present": bool(request.headers.get("cookie")),
        "last_property": request.session.get("last_property"),
        "verified_flags": {
            k: v for k, v in request.session.items()
            if str(k).startswith("guest_verified_")
        },
    }


# --- Basic routes ---
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


@app.get("/properties/{property_id}/guides")
def list_property_guides(
    property_id: int,
    db: Session = Depends(get_db),
):
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    guides = (
        db.query(Guide)
        .filter(
            Guide.property_id == property_id,
            Guide.is_active == True,
        )
        .order_by(Guide.sort_order.asc(), Guide.id.asc())
        .all()
    )

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
def chat(payload: ChatRequest, req: Request):
    client = get_openai(req)

    user_message = (payload.message or "").strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Message is required")

    model = (os.getenv("OPENAI_CHAT_MODEL") or "gpt-4o-mini").strip()

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.7,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": user_message},
            ],
        )
        return {"response": (resp.choices[0].message.content or "").strip()}

    except RateLimitError:
        logger.exception("OpenAI rate limit error")
        raise HTTPException(status_code=429, detail="Rate limit reached. Please try again shortly.")
    except AuthenticationError:
        logger.exception("OpenAI authentication error")
        raise HTTPException(status_code=500, detail="AI configuration error.")
    except APIStatusError as e:
        logger.exception("OpenAI API status error")
        code = int(getattr(e, "status_code", 502) or 502)
        raise HTTPException(status_code=code, detail="AI service temporarily unavailable.")
    except Exception:
        logger.exception("Unexpected /chat error")
        raise HTTPException(status_code=500, detail="Unexpected server error.")


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

'''
def maybe_autosummarize_on_new_guest_message(db: Session, session_id: int) -> None:
    """
    Re-run summary when new guest messages arrive, but throttle to avoid spam/cost.
    Skips resolved chats.
    """
    try:
        s = db.query(ChatSession).filter(ChatSession.id == int(session_id)).first()
        if not s:
            return

        if bool(getattr(s, "is_resolved", False)):
            return

        throttle_minutes = int(os.getenv("SUMMARY_THROTTLE_MINUTES", "10"))
        last = getattr(s, "ai_summary_updated_at", None)

        if last and (datetime.utcnow() - last) <= timedelta(minutes=throttle_minutes):
            return

        # force=False should respect your timestamp logic inside utils.ai_summary
        generate_and_store_summary(db=db, session_id=int(session_id), force=False)

    except Exception:
        logger.exception("Auto-summary failed (non-fatal)")
'''

def get_integration_for_property(db: Session, prop: Property) -> PMCIntegration:
    integration_id = getattr(prop, "integration_id", None)
    if not integration_id:
        raise HTTPException(status_code=400, detail="Property is missing integration_id")

    integ = (
        db.query(PMCIntegration)
        .filter(
            PMCIntegration.id == int(integration_id),
            PMCIntegration.pmc_id == int(prop.pmc_id),
        )
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



@app.get("/__routes")
def __routes():
    out = []
    for r in app.routes:
        methods = sorted(list(getattr(r, "methods", []) or []))
        out.append({"path": getattr(r, "path", None), "methods": methods, "name": getattr(r, "name", None)})
    return JSONResponse(out)



# ----------------------------
# Date helpers
# ----------------------------
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


def _to_date_any(x: Any) -> Optional[datetime.date]:
    """
    Normalize various inputs (datetime/date/str) -> date
    """
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.date()
    if hasattr(x, "year") and hasattr(x, "month") and hasattr(x, "day") and not isinstance(x, str):
        # already date-like (datetime.date)
        try:
            return x
        except Exception:
            return None
    if isinstance(x, str):
        return _parse_ymd(x)
    return None

def _day_range(d: datetime.date) -> tuple[datetime, datetime]:
    """UTC day range for a date: [00:00, next day 00:00)."""
    start = datetime(d.year, d.month, d.day)
    end = start + timedelta(days=1)
    return start, end


def turnover_on_arrival_day(
    db: Session,
    property_id: int,
    arrival_date: Any,
    guest_reservation_id: str | None = None,
) -> bool:
    """
    Early check-in is NOT available if someone else is DEPARTING on the guest's arrival date.
    Handles Reservation.departure_date stored as DATE or DATETIME.
    Filters out cancelled/non-blocking reservations when possible.
    """
    arrival = _to_date_any(arrival_date)
    if not arrival:
        return False

    q = db.query(Reservation).filter(
        Reservation.property_id == property_id,
        cast(Reservation.departure_date, Date) == arrival,
    )

    q = _apply_reservation_blocking_filters(q)

    if guest_reservation_id:
        # NOTE: this only excludes if your guest_reservation_id matches Reservation.id.
        # If your Reservation.id is internal DB id and guest_reservation_id is PMS id,
        # this won't exclude the same stay — which is fine for this query anyway.
        try:
            gid = int(str(guest_reservation_id))
            q = q.filter(Reservation.id != gid)
        except Exception:
            pass

    return db.query(q.exists()).scalar() is True


def turnover_on_departure_day(
    db: Session,
    property_id: int,
    departure_date: Any,
    guest_reservation_id: str | None = None,
) -> bool:
    """
    Late checkout is NOT available if someone else is ARRIVING on the guest's departure date.
    Handles Reservation.arrival_date stored as DATE or DATETIME.
    Filters out cancelled/non-blocking reservations when possible.
    """
    dep = _to_date_any(departure_date)
    if not dep:
        return False

    q = db.query(Reservation).filter(
        Reservation.property_id == property_id,
        cast(Reservation.arrival_date, Date) == dep,
    )

    q = _apply_reservation_blocking_filters(q)

    if guest_reservation_id:
        try:
            gid = int(str(guest_reservation_id))
            q = q.filter(Reservation.id != gid)
        except Exception:
            pass

    return db.query(q.exists()).scalar() is True


def _hostaway_turnover_flags(
    db: Session,
    prop: Property,
    arrival_date: Any,
    departure_date: Any,
    guest_reservation_id: str | None,
) -> tuple[bool, bool]:
    """
    Fallback turnover detection using Hostaway API when DB reservations are missing.
    Returns: (turnover_on_arrival, turnover_on_departure)
    """
    arrival = _to_date_any(arrival_date)
    dep = _to_date_any(departure_date)
    if not arrival and not dep:
        return (False, False)

    try:
        integ = get_integration_for_property(db, prop)
        if (integ.provider or "").strip().lower() != "hostaway":
            return (False, False)

        if not getattr(prop, "pms_property_id", None):
            return (False, False)

        account_id = (integ.account_id or "").strip()
        api_secret = (integ.api_secret or "").strip()
        if not account_id or not api_secret:
            return (False, False)

        token = get_token_for_pmc(account_id, api_secret)

        # Expand window to include far-future stays (like 2026)
        today = datetime.utcnow().date()
        max_needed = 0
        if arrival:
            max_needed = max(max_needed, (arrival - today).days)
        if dep:
            max_needed = max(max_needed, (dep - today).days)

        window_days = max(WINDOW_DAYS, max_needed + 30)
        window_days = min(window_days, 730)  # cap at 2 years to be safe

        reservations = fetch_reservations(
            listing_id=str(prop.pms_property_id),
            token=token,
            window_days=window_days,
            past_days=30,
        )

        turnover_arrival = False
        turnover_departure = False

        guest_id_str = str(guest_reservation_id).strip() if guest_reservation_id else ""

        for r in reservations or []:
            rid = str(r.get("id") or r.get("reservationId") or "").strip()
            if guest_id_str and rid and rid == guest_id_str:
                continue

            a = _parse_ymd(r.get("arrivalDate"))
            d = _parse_ymd(r.get("departureDate"))
            if not a or not d:
                continue

            # Someone departs on guest arrival day -> no early check-in
            if arrival and d == arrival:
                turnover_arrival = True

            # Someone arrives on guest departure day -> no late checkout
            if dep and a == dep:
                turnover_departure = True

            if turnover_arrival and turnover_departure:
                break

        return (turnover_arrival, turnover_departure)

    except Exception as e:
        logger.warning("[Hostaway turnover fallback failed] %r", e)
        return (False, False)

def _apply_reservation_blocking_filters(q):
    cols = {c.key for c in sa_inspect(Reservation).mapper.column_attrs}

    # statuses that should NOT block turnover logic
    bad_statuses = {"canceled", "cancelled", "inquiry", "expired", "declined"}

    # if there's a status-like field
    if "status" in cols:
        q = q.filter(~Reservation.status.in_(bad_statuses))
    if "reservation_status" in cols:
        q = q.filter(~Reservation.reservation_status.in_(bad_statuses))

    # block/hold types that should NOT count as an arrival turnover (optional)
    # (If you DO want blocks to block, remove this)
    bad_types = {"block", "blocked", "hold", "owner", "owner_stay", "maintenance"}
    if "type" in cols:
        q = q.filter(~Reservation.type.in_(bad_types))
    if "reservation_type" in cols:
        q = q.filter(~Reservation.reservation_type.in_(bad_types))
    if "kind" in cols:
        q = q.filter(~Reservation.kind.in_(bad_types))

    # boolean flags
    if "is_cancelled" in cols:
        q = q.filter(Reservation.is_cancelled.is_(False))
    if "cancelled_at" in cols:
        q = q.filter(Reservation.cancelled_at.is_(None))
    if "is_active" in cols:
        q = q.filter(Reservation.is_active.is_(True))

    return q


@app.get("/debug/turnover/{property_id}")
def debug_turnover(property_id: int, session_id: int | None = None, db: Session = Depends(get_db)):
    qs = db.query(ChatSession).filter(ChatSession.property_id == property_id)
    if session_id:
        qs = qs.filter(ChatSession.id == session_id)
    s = qs.order_by(ChatSession.last_activity_at.desc()).first()
    if not s:
        return {"error": "no session"}

    arr = _to_date_any(getattr(s, "arrival_date", None))
    dep = _to_date_any(getattr(s, "departure_date", None))

    dep_on_arrival_q = db.query(Reservation).filter(
        Reservation.property_id == property_id,
        cast(Reservation.departure_date, Date) == arr,
    )
    arr_on_departure_q = db.query(Reservation).filter(
        Reservation.property_id == property_id,
        cast(Reservation.arrival_date, Date) == dep,
    )

    dep_matches = dep_on_arrival_q.limit(20).all()
    arr_matches = arr_on_departure_q.limit(20).all()

    def pack(r: Reservation):
        cols = {c.key for c in sa_inspect(Reservation).mapper.column_attrs}
        def g(name):
            return getattr(r, name, None) if name in cols else None

        return {
            "id": g("id"),
            "property_id": g("property_id"),
            "arrival_date": str(_to_date_any(g("arrival_date"))),
            "departure_date": str(_to_date_any(g("departure_date"))),
            "status": g("status") or g("reservation_status"),
            "type": g("type") or g("reservation_type") or g("kind"),
            "is_active": g("is_active"),
            "is_cancelled": g("is_cancelled"),
            "source": g("source") or g("provider"),
            "pms_reservation_id": g("pms_reservation_id") or g("reservation_id") or g("external_id"),
        }

    return {
        "session_id": s.id,
        "arrival_date": str(arr),
        "departure_date": str(dep),
        "departures_on_arrival_day_count": dep_on_arrival_q.count(),
        "arrivals_on_departure_day_count": arr_on_departure_q.count(),
        "departures_on_arrival_day_sample": [pack(x) for x in dep_matches],
        "arrivals_on_departure_day_sample": [pack(x) for x in arr_matches],
    }


# ----------------------------
# Price formatting helper
# ----------------------------
def format_price_display(up: Upgrade) -> str:
    currency = (getattr(up, "currency", None) or "usd").lower()
    cents = getattr(up, "price_cents", None)

    if cents is None:
        val = getattr(up, "price_display", None)
        return str(val) if val else ""

    try:
        amount = float(cents) / 100.0
    except Exception:
        return ""

    symbol = "$" if currency in ("usd", "us$", "$") else ""
    return f"{symbol}{amount:,.2f}" if symbol else f"{amount:,.2f} {currency.upper()}"


# ----------------------------
# Guest UI route (FINAL / CLEAN + FIXED)
# - Restores image context keys so photos load
# - Keeps VERIFIED session as the ONLY source of turnover gating
# - Adds safe hero fallbacks (feature -> hero)
# - (Optional) keeps Hostaway listing overview hero override (doesn't affect turnover)
# ----------------------------
@app.get("/guest/{property_id}", response_class=HTMLResponse)
def guest_app_ui(request: Request, property_id: int, db: Session = Depends(get_db)):
    request.session["last_property"] = property_id

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    # HARD GATE
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
                    This property hasn’t enabled Sandy yet. Please contact your host.
                  </p>
                </div>
              </body>
            </html>
            """,
            status_code=403,
        )

    pmc = getattr(prop, "pmc", None)
    is_live = bool(getattr(prop, "sandy_enabled", False) and pmc and getattr(pmc, "active", False))

    # ----------------------------
    # Load config
    # ----------------------------
    context = load_property_context(prop, db)
    cfg = (context.get("config") or {}) if isinstance(context, dict) else {}
    wifi = cfg.get("wifi") or {}
    assistant_config = cfg.get("assistant") if isinstance(cfg.get("assistant"), dict) else {}

    address = cfg.get("address")
    city_name = cfg.get("city_name")

    # ✅ Restore image keys your template likely expects
    feature_image_url = cfg.get("feature_image_url")
    family_image_url = cfg.get("family_image_url")
    foodie_image_url = cfg.get("foodie_image_url")

    # ✅ Strong hero fallbacks so photos don't "disappear"
    hero_image_url = cfg.get("hero_image_url") or feature_image_url or family_image_url or foodie_image_url
    experiences_hero_url = cfg.get("experiences_hero_url") or hero_image_url

    # (Optional) keep Hostaway listing overview override for hero/address/city
    prop_provider = (
        (getattr(prop, "provider", None) or getattr(prop, "pms_integration", None) or "")
        .strip()
        .lower()
    )
    if prop_provider == "hostaway" and getattr(prop, "pms_property_id", None):
        try:
            integ = get_integration_for_property(db, prop)
            if (getattr(integ, "provider", "") or "").strip().lower() == "hostaway":
                hero, ha_address, ha_city = get_listing_overview(
                    listing_id=str(prop.pms_property_id),
                    client_id=(integ.account_id or "").strip(),
                    client_secret=(integ.api_secret or "").strip(),
                )
                if hero and not hero_image_url:
                    hero_image_url = hero
                if hero and not experiences_hero_url:
                    experiences_hero_url = hero
                if ha_address and not address:
                    address = ha_address
                if ha_city and not city_name:
                    city_name = ha_city
        except Exception as e:
            logger.warning("[Hostaway] Listing overview failed: %r", e)

    if not experiences_hero_url and hero_image_url:
        experiences_hero_url = hero_image_url

    # ----------------------------
    # VERIFIED SESSION ONLY (for turnover + dates)
    # ----------------------------
    # ----------------------------
# Session resolution (STABLE)
# ----------------------------
verified_session_id = request.session.get(f"guest_session_{property_id}")

active_session = None

if verified_session_id:
    try:
        active_session = (
            db.query(ChatSession)
            .filter(
                ChatSession.id == int(verified_session_id),
                ChatSession.property_id == prop.id,
            )
            .first()
        )
    except Exception:
        active_session = None

# ✅ Fallback ONLY if no verified session
if not active_session:
    active_session = (
        db.query(ChatSession)
        .filter(ChatSession.property_id == prop.id)
        .order_by(ChatSession.last_activity_at.desc())
        .first()
    )


    reservation_name = (
        (getattr(active_session, "guest_name", None) if active_session else None)
        or (getattr(latest_session, "guest_name", None) if latest_session else None)
    )

    arrival_date = getattr(active_session, "arrival_date", None) if active_session else None
    departure_date = getattr(active_session, "departure_date", None) if active_session else None
    reservation_id = getattr(active_session, "reservation_id", None) if active_session else None

    # ----------------------------
    # TURNOVER (DB ONLY — VERIFIED SESSION ONLY)
    # ----------------------------
    turnover_on_arrival = False
    turnover_on_departure = False

    if active_session:
        turnover_on_arrival = turnover_on_arrival_day(db, prop.id, arrival_date, reservation_id)
        turnover_on_departure = turnover_on_departure_day(db, prop.id, departure_date, reservation_id)

    # ----------------------------
    # UPGRADES (PER-ITEM AVAILABILITY)
    # ----------------------------
    upgrades = (
        db.query(Upgrade)
        .filter(Upgrade.property_id == prop.id, Upgrade.is_active.is_(True))
        .order_by(Upgrade.sort_order.asc(), Upgrade.id.asc())
        .all()
    )

    visible_upgrades = []
    for up in upgrades:
        is_available = True
        unavailable_reason = ""

        is_time_flex, kind = _is_time_flex_upgrade(up)

        if is_time_flex and kind == "early_checkin" and turnover_on_arrival:
            is_available = False
            unavailable_reason = "Not available for same-day turnovers."
        elif is_time_flex and kind == "late_checkout" and turnover_on_departure:
            is_available = False
            unavailable_reason = "Not available for same-day turnovers."

        visible_upgrades.append(
            {
                "id": up.id,
                "slug": up.slug,
                "title": up.title,
                "short_description": up.short_description,
                "long_description": up.long_description,
                "price_cents": up.price_cents,
                "price_currency": up.currency or "usd",
                "price_display": format_price_display(up),
                "stripe_price_id": up.stripe_price_id,
                "image_url": getattr(up, "image_url", None),
                "badge": getattr(up, "badge", None),
                "is_available": bool(is_available),
                "unavailable_reason": unavailable_reason,
            }
        )

    # ----------------------------
    # UI helpers
    # ----------------------------
    google_maps_link = None
    if address or city_name:
        q = " ".join(filter(None, [address, city_name]))
        google_maps_link = f"https://www.google.com/maps/search/?api=1&query={quote_plus(q)}"

    checkin_time = hour_to_ampm(cfg.get("checkInTimeStart") or cfg.get("checkinTimeStart"))
    checkout_time = hour_to_ampm(cfg.get("checkOutTime") or cfg.get("checkoutTime"))

    # ----------------------------
    # RENDER
    # ----------------------------
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
            "checkin_time": checkin_time,
            "checkout_time": checkout_time,
            "arrival_date": arrival_date,
            "departure_date": departure_date,
            "city_name": city_name,

            # ✅ restore photo keys (template dependencies)
            "feature_image_url": feature_image_url,
            "family_image_url": family_image_url,
            "foodie_image_url": foodie_image_url,
            "hero_image_url": hero_image_url,
            "experiences_hero_url": experiences_hero_url,

            "google_maps_link": google_maps_link,
            "is_live": is_live,
            "sandy_enabled": bool(getattr(prop, "sandy_enabled", False)),
            "is_verified": request.session.get(f"guest_verified_{property_id}", False),

            # ✅ this must exist so JS can set currentSessionId safely
            "initial_session_id": verified_session_id,

            "assistant_config": assistant_config,
            "upgrades": visible_upgrades,
            "turnover_on_arrival": turnover_on_arrival,
            "turnover_on_departure": turnover_on_departure,
        },
    )



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

class VerifyRequest(BaseModel):
    code: str

@app.post("/guest/{property_id}/verify-json")
def verify_json(
    property_id: int,
    payload: VerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    WINDOW_DAYS = int(os.getenv("WINDOW_DAYS", "120"))
    code = (payload.code or "").strip()

    if not code.isdigit() or len(code) != 4:
        return JSONResponse(
            {"success": False, "error": "Please enter exactly 4 digits."},
            status_code=400,
        )

    prop = db.query(Property).filter(Property.id == int(property_id)).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    pmc = getattr(prop, "pmc", None)
    if not pmc:
        return JSONResponse(
            {"success": False, "error": "This property is not linked to a PMC."},
            status_code=400,
        )

    # --- Test unlock bypass ---
    test_code = (os.getenv("TEST_UNLOCK_CODE") or "").strip()
    if test_code and code == test_code:
        request.session[f"guest_verified_{property_id}"] = True

        today = datetime.utcnow().date()
        arrival = today.strftime("%Y-%m-%d")
        departure = (today + timedelta(days=3)).strftime("%Y-%m-%d")

        now = datetime.utcnow()
        session = ChatSession(
            property_id=property_id,
            source="guest_web",
            is_verified=True,
            created_at=now,
            last_activity_at=now,
        )

        if hasattr(session, "guest_name"):
            session.guest_name = "Test Guest"
        if hasattr(session, "arrival_date"):
            session.arrival_date = arrival
        if hasattr(session, "departure_date"):
            session.departure_date = departure
        if hasattr(session, "phone_last4"):
            session.phone_last4 = code

        db.add(session)
        db.commit()
        db.refresh(session)

        request.session[f"guest_session_{property_id}"] = session.id

        return {
            "success": True,
            "session_id": session.id,
            "guest_name": "Test Guest",
            "arrival_date": arrival,
            "departure_date": departure,
            "checkin_time": "4:00 PM",
            "checkout_time": "10:00 AM",
        }

    # --- Resolve integration/provider ---
    try:
        integ = get_integration_for_property(db, prop)
    except Exception:
        integ = None

    prop_provider = (getattr(prop, "provider", None) or "").strip().lower()
    integ_provider = (getattr(integ, "provider", None) or "").strip().lower()
    provider = prop_provider or integ_provider

    phone_last4 = None
    reservation_id = None
    guest_name = None
    arrival_date = None
    departure_date = None

    try:
        # --- Hostaway: match reservation by CODE (last4) ---
        if provider == "hostaway" and getattr(prop, "pms_property_id", None):
            account_id = (getattr(integ, "account_id", None) or "").strip()
            api_secret = (getattr(integ, "api_secret", None) or "").strip()

            if not account_id or not api_secret:
                raise Exception("Missing Hostaway creds on integration (account_id/api_secret)")

            token = get_token_for_pmc(account_id, api_secret)
            reservations = fetch_reservations(
                listing_id=str(prop.pms_property_id),
                token=token,
                window_days=WINDOW_DAYS,
                past_days=30,
            )

            today = datetime.utcnow().date()
            best = None
            best_days = None

            for r in reservations:
                full_phone = (
                    r.get("phone")
                    or r.get("guestPhone")
                    or r.get("guestPhoneNumber")
                    or ""
                )
                digits = "".join(ch for ch in full_phone if ch.isdigit())
                if len(digits) < 4:
                    continue

                # ✅ Must match the entered code
                if digits[-4:] != code:
                    continue

                checkin_str = r.get("arrivalDate")
                if not checkin_str:
                    continue

                try:
                    checkin = datetime.strptime(checkin_str, "%Y-%m-%d").date()
                except Exception:
                    continue

                days_until = (checkin - today).days
                if days_until < 0:
                    continue
                if days_until > WINDOW_DAYS:
                    continue

                if best is None or days_until < best_days:
                    best = r
                    best_days = days_until

            if not best:
                return JSONResponse(
                    {"success": False, "error": "No upcoming reservation found matching that code."},
                    status_code=400,
                )

            phone_last4 = code
            reservation_id = str(best.get("id") or best.get("reservationId") or "")
            guest_name = best.get("guestName") or best.get("name") or None
            arrival_date = best.get("arrivalDate")
            departure_date = best.get("departureDate")

        # --- Other PMS providers: keep existing behavior ---
        else:
            (
                phone_last4,
                _door_code,
                reservation_id,
                guest_name,
                arrival_date,
                departure_date,
            ) = get_pms_access_info(pmc, prop)

    except Exception as e:
        logger.warning("[VERIFY PMS ERROR] %r", e)
        return JSONResponse(
            {"success": False, "error": "Could not verify your reservation. Please try again."},
            status_code=500,
        )

    # --- Validate arrival window (belt + suspenders) ---
    today = datetime.utcnow().date()
    arrival_obj = _parse_ymd(arrival_date)

    if arrival_obj and arrival_obj > today + timedelta(days=WINDOW_DAYS):
        return JSONResponse(
            {"success": False, "error": f"You can only unlock this stay within {WINDOW_DAYS} days of arrival."},
            status_code=400,
        )

    if not phone_last4 or not reservation_id:
        return JSONResponse(
            {"success": False, "error": "No upcoming reservation found for this property."},
            status_code=400,
        )

    phone_last4 = str(phone_last4).strip()
    if code != phone_last4:
        return JSONResponse(
            {"success": False, "error": "That code does not match the reservation phone number."},
            status_code=403,
        )

    checkin_time_display = _format_time_display(getattr(prop, "checkin_time", None), default="4:00 PM")
    checkout_time_display = _format_time_display(getattr(prop, "checkout_time", None), default="10:00 AM")

    # --- Mark verified + create session ---
    request.session[f"guest_verified_{property_id}"] = True

    now = datetime.utcnow()
    session = ChatSession(
        property_id=property_id,
        source="guest_web",
        is_verified=True,
        created_at=now,
        last_activity_at=now,
    )

    if hasattr(session, "guest_name"):
        session.guest_name = (guest_name or "Guest").strip()
    if hasattr(session, "arrival_date"):
        session.arrival_date = arrival_date
    if hasattr(session, "departure_date"):
        session.departure_date = departure_date
    if hasattr(session, "phone_last4"):
        session.phone_last4 = phone_last4
    if hasattr(session, "reservation_id"):
        session.reservation_id = reservation_id

    db.add(session)
    db.commit()
    db.refresh(session)

    request.session[f"guest_session_{property_id}"] = session.id

    return {
        "success": True,
        "session_id": session.id,
        "guest_name": guest_name or "Guest",
        "arrival_date": arrival_date,
        "departure_date": departure_date,
        "checkin_time": checkin_time_display,
        "checkout_time": checkout_time_display,
        "reservation_id": reservation_id,
    }


# --- property chat request (kept as-is for your frontend payload shape) ---
class PropertyChatRequest(BaseModel):
    message: str
    session_id: Optional[int] = None
    language: Optional[str] = None
    thread_id: Optional[str] = None
    client_message_id: Optional[str] = None
    parent_id: Optional[str] = None


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




AFFIRMATIONS = {"yes", "y", "yeah", "yep", "sure", "ok", "okay", "please", "sounds good"}
NEGATIONS = {"no", "n", "nope", "not now", "nah"}

def _safe_role(value: str) -> str:
    v = (value or "").strip().lower()
    if v in {"user", "assistant", "system"}:
        return v
    return "user"


def _chatmessage_columns() -> set[str]:
    try:
        return {attr.key for attr in sa_inspect(ChatMessage).mapper.column_attrs}
    except Exception:
        return set()

_CHATMSG_COLS = _chatmessage_columns()

def _pick_first(d: dict, keys: list[str]):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None

def _msg_get_role(m: ChatMessage) -> str:
    # Try common role/sender fields, fallback to "user"
    for key in ["role", "sender", "message_role", "author", "from_role", "kind"]:
        if hasattr(m, key):
            v = (getattr(m, key) or "").strip().lower()
            if v in {"user", "assistant", "system"}:
                return v

    # Try boolean flags
    for key in ["is_user", "from_user", "from_guest", "guest", "user"]:
        if hasattr(m, key):
            try:
                return "user" if bool(getattr(m, key)) else "assistant"
            except Exception:
                pass

    return "user"

def _msg_get_content(m: ChatMessage) -> str:
    for key in ["content", "message", "text", "body", "message_text"]:
        if hasattr(m, key):
            v = getattr(m, key)
            return (v or "").strip()
    return ""

def _new_chat_message(session_id: int, role: str, content: str) -> ChatMessage:
    """
    Create ChatMessage using only columns that exist on your model.
    This avoids TypeError('role' invalid kwarg) etc.
    """
    now = datetime.utcnow()
    data = {}

    # session link column (try common names)
    if "session_id" in _CHATMSG_COLS:
        data["session_id"] = session_id
    elif "chat_session_id" in _CHATMSG_COLS:
        data["chat_session_id"] = session_id

    # content column (try common names)
    if "content" in _CHATMSG_COLS:
        data["content"] = content
    elif "message" in _CHATMSG_COLS:
        data["message"] = content
    elif "text" in _CHATMSG_COLS:
        data["text"] = content
    elif "body" in _CHATMSG_COLS:
        data["body"] = content
    elif "message_text" in _CHATMSG_COLS:
        data["message_text"] = content

    # role/sender column (try common names)
    if "role" in _CHATMSG_COLS:
        data["role"] = role
    elif "sender" in _CHATMSG_COLS:
        data["sender"] = role
    elif "message_role" in _CHATMSG_COLS:
        data["message_role"] = role
    elif "author" in _CHATMSG_COLS:
        data["author"] = role
    else:
        # boolean style fallback
        if "is_user" in _CHATMSG_COLS:
            data["is_user"] = (role == "user")
        elif "from_user" in _CHATMSG_COLS:
            data["from_user"] = (role == "user")
        elif "from_guest" in _CHATMSG_COLS:
            data["from_guest"] = (role == "user")

    # timestamp column (optional)
    if "created_at" in _CHATMSG_COLS:
        data["created_at"] = now
    elif "timestamp" in _CHATMSG_COLS:
        data["timestamp"] = now
    elif "created" in _CHATMSG_COLS:
        data["created"] = now

    return ChatMessage(**data)





@app.post("/properties/{property_id}/chat")
def property_chat(
    property_id: int,
    payload: PropertyChatRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    # 1) require unlock
    if not request.session.get(f"guest_verified_{property_id}", False):
        raise HTTPException(status_code=403, detail="Please unlock your stay first.")

    # 2) validate property exists
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    # 3) session id (payload OR cookie session)
    session_id = payload.session_id or request.session.get(f"guest_session_{property_id}")
    session = None
    now = datetime.utcnow()

    if session_id:
        session = db.query(ChatSession).filter(ChatSession.id == int(session_id)).first()

    if not session:
        session = ChatSession(
            property_id=property_id,
            source="guest_web",
            is_verified=True,
            created_at=now,
            last_activity_at=now,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id = session.id
        request.session[f"guest_session_{property_id}"] = session_id

    # 4) load context + build system prompt
    context = load_property_context(prop, db)
    pmc = getattr(prop, "pmc", None)

    system_prompt = build_system_prompt(
        prop,
        pmc,
        context,
        payload.language,
        session,
        is_verified=True,
    )

    # 5) user message
    user_message = (payload.message or "").strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Message is required")

    # 6) Pull recent history
    HISTORY_LIMIT = 12
    history_rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    if len(history_rows) > HISTORY_LIMIT:
        history_rows = history_rows[-HISTORY_LIMIT:]

    # Build OpenAI messages list
    messages = [{"role": "system", "content": system_prompt}]
    for m in history_rows:
        sender = (getattr(m, "sender", "") or "").lower().strip()
        content = (getattr(m, "content", "") or "").strip()
        if not content:
            continue
        messages.append(
            {"role": "assistant", "content": content}
            if sender == "assistant"
            else {"role": "user", "content": content}
        )

    messages.append({"role": "user", "content": user_message})

    # 7) Call OpenAI (assistant response)
    client = get_openai(request)
    model = (os.getenv("OPENAI_CHAT_MODEL") or "gpt-4o-mini").strip()

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.5,
            messages=messages,
        )

        assistant_text = (resp.choices[0].message.content or "").strip()
        assistant_text = enforce_click_here_links(assistant_text)

        # 8) Sentiment tagging (OpenAI-first + fallback) using conversation context
        sent = classify_guest_sentiment(client, history_rows, user_message)

        sentiment_label = (sent.get("sentiment") or "neutral").lower().strip()
        if sentiment_label not in {"positive", "neutral", "negative"}:
            sentiment_label = "neutral"

        # keep sentiment_data JSON-serializable
        sentiment_data = {
            "mood": sent.get("mood"),
            "confidence": sent.get("confidence"),
            "source": sent.get("source"),
            "flags": sent.get("flags", {}),
        }

        # 9) Save guest message WITH sentiment + sentiment_data
        db.add(
            ChatMessage(
                session_id=session_id,
                sender="user",
                content=user_message,
                created_at=now,
                sentiment=sentiment_label,     # ✅ string only
                sentiment_data=sentiment_data, # ✅ JSONB
            )
        )

        # Save assistant message
        db.add(
            ChatMessage(
                session_id=session_id,
                sender="assistant",
                content=assistant_text,
                created_at=datetime.utcnow(),
            )
        )

        # Update session activity
        session.last_activity_at = datetime.utcnow()
        db.add(session)
        db.commit()

        # Auto summary (non-fatal)
        try:
            maybe_autosummarize_on_new_guest_message(db, session_id=int(session_id))
        except Exception:
            logger.exception("Auto-summary failed (non-fatal)")

        return {
            "response": assistant_text,
            "session_id": session_id,
            "thread_id": payload.thread_id,
            "reply_to": payload.client_message_id,
            "suggestions": [],
        }

    except RateLimitError:
        raise HTTPException(status_code=429, detail="Rate limit reached. Please try again shortly.")
    except AuthenticationError:
        raise HTTPException(status_code=500, detail="AI configuration error.")
    except APIStatusError as e:
        code = int(getattr(e, "status_code", 502) or 502)
        raise HTTPException(status_code=code, detail="AI service temporarily unavailable.")
    except Exception:
        logger.exception("Unexpected property_chat error")
        raise HTTPException(status_code=500, detail="Unexpected server error.")


def parse_ts(x: str | int | None) -> int | None:
    if x is None:
        return None
    v = int(x)
    # if it looks like milliseconds, convert to seconds
    if v > 10_000_000_000:
        v = v // 1000
    return v

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
    text = (message or "").lower()

    negative_markers = [
        "terrible", "awful", "angry", "mad", "furious", "pissed",
        "bad", "disappointed", "upset", "frustrated", "annoyed",
        "unacceptable", "worst",
    ]
    positive_markers = [
        "great", "amazing", "awesome", "love", "fantastic",
        "perfect", "thank you", "thanks", "appreciate",
    ]

    if any(w in text for w in negative_markers):
        return "negative"
    if any(w in text for w in positive_markers):
        return "positive"
    return "neutral"



def get_today_reservation(db: Session, property_id: int) -> Reservation | None:
    today = datetime.utcnow().date()

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
        p = (path or "").strip()
        if not p:
            return ""
        if os.path.isabs(p):
            return p
        if not DATA_REPO_DIR:
            return p
        return os.path.join(DATA_REPO_DIR, p)

    used_default_cfg = False
    used_default_manual = False
    resolved_from = "none"

    base_dir = _abs_in_repo(getattr(prop, "data_folder_path", None) or "")
    if base_dir:
        resolved_from = "prop.data_folder_path"

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
            base_dir = os.path.join(
                DATA_REPO_DIR,
                "data",
                f"{provider}_{account_id}",
                f"{provider}_{pms_property_id}",
            )
            resolved_from = "computed(provider+account_id+pms_property_id)"

    config: Dict[str, Any] = {}
    manual_text: str = ""

    if base_dir:
        cfg_path = os.path.join(base_dir, "config.json")
        man_path = os.path.join(base_dir, "manual.txt")

        if os.path.exists(cfg_path):
            config = _read_json(cfg_path)
        if os.path.exists(man_path):
            manual_text = _read_text(man_path)

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
    is_verified: bool = False,
) -> str:
    config = context.get("config", {}) or {}
    manual = context.get("manual", "") or ""

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

    # Only include private stay details if verified
    guest_block = ""
    if is_verified and session:
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
""".strip()

    lang_code = (session_language or "").strip().lower()
    if not lang_code or lang_code == "auto":
        language_instruction = "Always answer in the SAME language the guest uses."
        lang_label = "auto"
    else:
        lang_label = lang_code
        language_instruction = f"Always answer in {lang_code.upper()} unless the guest clearly switches languages."

    verification_line = "VERIFIED" if is_verified else "NOT VERIFIED"

    return f"""
        You are {assistant_name}, an AI concierge for "{prop.property_name}".
        
        Context:
        - Property host/manager: {getattr(pmc, "pmc_name", None) if pmc else "Unknown PMC"}
        - Emergency or urgent issues: {emergency_phone} (phone)
        
        Language:
        - Guest preferred language setting: {lang_label}
        - {language_instruction}
        
        Guest access:
        - Verification status: {verification_line}
        - If NOT VERIFIED: refuse and ask them to unlock first.
        - If VERIFIED: you may answer normally and may share verified stay details ONLY if the guest asks.
        
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
        - Do NOT greet the guest with “Hello”, “Hi”, or “How can I help?” unless this is the FIRST message of the conversation. Continue naturally from the existing context.
        - If the guest replies with a short confirmation (e.g., “yes”, “ok”, “sounds good”), assume it refers to your most recent suggestion.

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
