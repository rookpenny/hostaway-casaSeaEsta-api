import os
import stripe
import secrets
from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks, Form
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from starlette.config import Config
from authlib.integrations.starlette_client import OAuth

from sqlalchemy.orm import Session
from sqlalchemy import func

from database import SessionLocal, get_db
from models import PMC, Property, PMCUser
from utils.pms_sync import sync_properties  # sync by account_id

from utils.billing import sync_property_quantity
from utils.billing_guard import require_pmc_is_paid

from starlette.background import BackgroundTasks

router = APIRouter(prefix="/auth")
templates = Jinja2Templates(directory="templates")


# ----------------------------
# OAuth Config
# ----------------------------
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

config = Config(
    environ={
        "GOOGLE_CLIENT_ID": GOOGLE_CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": GOOGLE_CLIENT_SECRET,
    }
)

oauth = OAuth(config)
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)



# ----------------------------
# Login scope resolution
# ----------------------------
def is_super_admin_email(email: str) -> bool:
    allow = os.getenv("ADMIN_EMAILS", "")
    if not allow.strip():
        return False
    allowed = {e.strip().lower() for e in allow.split(",") if e.strip()}
    return (email or "").strip().lower() in allowed


def resolve_login_scope(email: str) -> Dict[str, Any]:
    """
    Returns:
      {
        "ok": bool,
        "role": "super" | "pmc" | None,
        "pmc_id": int | None,
        "pmc_user_id": int | None,
        "error": str | None
      }
    """
    email_l = (email or "").strip().lower()
    if not email_l:
        return {"ok": False, "role": None, "pmc_id": None, "pmc_user_id": None, "error": "No email"}

    if is_super_admin_email(email_l):
        return {"ok": True, "role": "super", "pmc_id": None, "pmc_user_id": None, "error": None}

    db = SessionLocal()
    try:
        # 1) PMC staff membership (preferred)
        pmc_user = (
            db.query(PMCUser)
            .filter(func.lower(PMCUser.email) == email_l, PMCUser.is_active == True)
            .first()
        )
        if pmc_user:
            pmc = db.query(PMC).filter(PMC.id == pmc_user.pmc_id).first()
            if pmc and pmc.active:
                return {
                    "ok": True,
                    "role": "pmc",
                    "pmc_id": pmc.id,
                    "pmc_user_id": pmc_user.id,
                    "error": None,
                }
            return {"ok": False, "role": None, "pmc_id": None, "pmc_user_id": None, "error": "PMC is inactive"}

        # 2) fallback: PMC owner email on PMC table
        pmc = (
            db.query(PMC)
            .filter(func.lower(PMC.email) == email_l, PMC.active == True)
            .first()
        )
        if pmc:
            return {"ok": True, "role": "pmc", "pmc_id": pmc.id, "pmc_user_id": None, "error": None}

        return {"ok": False, "role": None, "pmc_id": None, "pmc_user_id": None, "error": "Unauthorized email"}
    finally:
        db.close()



@router.post("/login/email")
async def login_with_email(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks | None = None,
):
    # Normalize and validate the email
    email_l = (email or "").strip().lower()
    if not email_l:
        return HTMLResponse("<h2>Invalid email address.</h2>", status_code=400)

    # Look up the PMC or PMCUser by email (reuse resolve_login_scope)
    scope = resolve_login_scope(email_l)
    if not scope["ok"]:
        # Generic error avoids revealing whether the email exists
        return HTMLResponse(
            "<h2>We couldn’t find that email. Please check and try again.</h2>",
            status_code=403,
        )

    # Generate a one‑time token and store it in the session
    token = secrets.token_urlsafe(32)
    request.session["email_login_token"] = token
    request.session["email_login_target"] = email_l

    # Build the magic link
    app_base = (os.getenv("APP_BASE_URL") or "").rstrip("/")
    magic_url = f"{app_base}/auth/email-callback?token={token}"

    # Queue email sending (placeholder implementation)
    if background_tasks:
        background_tasks.add_task(
            send_magic_email,
            to=email_l,
            magic_url=magic_url,
        )

    # Show instructions to check the user’s inbox
    return templates.TemplateResponse(
        "login_email_sent.html",
        {"request": request, "email": email_l},
    )


# ----------------------------
# Scope helpers (auth-side)
# ----------------------------
def require_property_in_scope(request: Request, db: Session, property_id: int) -> Property:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=403, detail="Unauthorized")

    role = request.session.get("role")
    pmc_id = request.session.get("pmc_id")

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    if role == "super":
        return prop

    if role == "pmc":
        if not pmc_id:
            raise HTTPException(status_code=403, detail="PMC scope missing")
        if int(prop.pmc_id) != int(pmc_id):
            raise HTTPException(status_code=403, detail="Forbidden")
        return prop

    raise HTTPException(status_code=403, detail="Forbidden")


def get_properties_for_pmc(email: str) -> List[Property]:
    """Not currently used in your routes, but fixed + safe."""
    email_l = (email or "").strip().lower()
    if not email_l:
        return []

    db: Session = SessionLocal()
    try:
        pmc = db.query(PMC).filter(func.lower(PMC.email) == email_l).first()
        if not pmc:
            return []
        return list(pmc.properties or [])
    finally:
        db.close()


# ----------------------------
# Routes
# ----------------------------
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    # sanity: if Google env missing, show readable error in UI
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return HTMLResponse(
            "<h2>OAuth not configured</h2><p>Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET</p>",
            status_code=500,
        )
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/login/google")
async def login_with_google(request: Request, next: str = "/admin/dashboard"):
    """
    Starts Google OAuth and remembers where to send the user afterward.
    IMPORTANT: public signup must call /auth/login/google?next=/pmc/signup
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return HTMLResponse(
            "<h2>OAuth not configured</h2><p>Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET</p>",
            status_code=500,
        )

    # ✅ Only allow safe internal redirects
    next_clean = (next or "/admin/dashboard").strip()
    if not next_clean.startswith("/"):
        next_clean = "/admin/dashboard"
    if next_clean.startswith("//") or next_clean.startswith("/\\"):
        next_clean = "/admin/dashboard"

    request.session["post_login_redirect"] = next_clean

    redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def auth_callback(request: Request):
    """
    Google OAuth callback.

    Behavior:
    - If user is an authorized Super user or PMC user -> normal login.
    - If user is NOT authorized but they were trying to reach /pmc/signup -> allow a limited "signup" session.
    - Otherwise -> 403.
    """
    try:
        token = await oauth.google.authorize_access_token(request)
        userinfo = await oauth.google.userinfo(token=token)

        email = (userinfo.get("email") or "").strip()
        name = (userinfo.get("name") or "").strip() or None

        if not email:
            return HTMLResponse(
                "<h2>Access denied: No email returned from Google</h2>",
                status_code=400,
            )

        email_l = email.lower()

        # Where should we send them after login?
        next_url = request.session.get("post_login_redirect") or "/admin/dashboard"
        request.session.pop("post_login_redirect", None)

        # Check authorization scope
        scope = resolve_login_scope(email_l)

        # ✅ If unauthorized BUT they were headed to public signup, allow limited session
        if not scope["ok"]:
            # Allow limited signup session when the user was trying to reach /pmc/signup
            if (next_url or "").startswith("/pmc/signup"):
                # Base identity (so signup can prefill + lock email)
                request.session["user"] = {"email": email_l, "name": name}
                request.session["admin_email"] = email_l
        
                # Limited role/scope (no PMC access yet)
                request.session["role"] = "signup"
                request.session["pmc_id"] = None
                request.session["pmc_user_id"] = None
        
                return RedirectResponse(url=next_url, status_code=302)
        
            # Render a friendly access-denied page using a template
            return templates.TemplateResponse(
                "access_denied.html",
                {
                    "request": request,
                    "error": scope.get("error") or "Unauthorized email",
                    "email": email_l,
                },
                status_code=403,
            )


        # ✅ Normal authorized login
        request.session["user"] = {"email": email_l, "name": name}
        request.session["admin_email"] = email_l  # matches ADMIN_IDENTITY_SESSION_KEY default

        request.session["role"] = scope["role"]          # "super" | "pmc"
        request.session["pmc_id"] = scope["pmc_id"]
        request.session["pmc_user_id"] = scope["pmc_user_id"]

        return RedirectResponse(url=next_url, status_code=302)

    except Exception as e:
        print("[OAuth Error]", e)
        return HTMLResponse(f"<h2>OAuth Error: {e}</h2>", status_code=500)


@router.get("/dashboard")
def dashboard(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/auth/login", status_code=302)
    return RedirectResponse(url="/admin/dashboard", status_code=302)



@router.post("/toggle-property/{property_id}")
def toggle_property(property_id: int, request: Request, db: Session = Depends(get_db)):
    prop = require_property_in_scope(request, db, property_id)

    # ✅ Paywall: initial signup fee must be paid
    require_pmc_is_paid(db, prop.pmc_id)

    pmc = db.query(PMC).filter(PMC.id == prop.pmc_id).first()
    if not pmc:
        raise HTTPException(status_code=404, detail="PMC not found")

    previous = bool(prop.sandy_enabled)
    prop.sandy_enabled = not previous
    db.commit()

    # If turning OFF: just sync billing (next renewal adjusts; no mid-cycle proration)
    if prop.sandy_enabled is False:
        try:
            sync_property_quantity(db, pmc.id, proration_behavior="none")
        except Exception as e:
            prop.sandy_enabled = previous
            db.commit()
            raise HTTPException(status_code=500, detail=f"Billing update failed, change reverted: {str(e)}")

        return JSONResponse({"status": "success", "new_status": "OFFLINE"})

    # If turning ON: compute how many will be LIVE (billable) if we proceed
    enabled_count = (
        db.query(func.count(Property.id))
        .filter(Property.pmc_id == pmc.id, Property.sandy_enabled.is_(True))
        .scalar()
    ) or 0
    enabled_count = int(enabled_count)

    # Stripe config
    stripe_secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    price_property = (os.getenv("STRIPE_PRICE_PROPERTY_MONTHLY") or "").strip()
    app_base_url = (os.getenv("APP_BASE_URL") or "").rstrip("/")

    if not stripe_secret or not price_property or not app_base_url:
        prop.sandy_enabled = previous
        db.commit()
        raise HTTPException(status_code=500, detail="Missing Stripe env vars required for billing")

    stripe.api_key = stripe_secret

    subscription_id = getattr(pmc, "stripe_subscription_id", None)
    customer_id = getattr(pmc, "stripe_customer_id", None)

    # If no customer id, we can't start subscription checkout
    if not customer_id:
        prop.sandy_enabled = previous
        db.commit()
        raise HTTPException(status_code=500, detail="Missing stripe_customer_id on PMC (signup checkout must create a customer)")

    # If no subscription, or subscription is fully canceled => require checkout to restart billing
    needs_new_checkout = False
    if not subscription_id:
        needs_new_checkout = True
    else:
        try:
            sub = stripe.Subscription.retrieve(subscription_id)
            status = (sub.get("status") or "").lower()
            if status in {"canceled", "incomplete_expired"}:
                needs_new_checkout = True
        except Exception as e:
            prop.sandy_enabled = previous
            db.commit()
            raise HTTPException(status_code=500, detail=f"Stripe subscription lookup failed: {str(e)}")

    if needs_new_checkout:
        # Revert toggle so they don't go LIVE without billing
        prop.sandy_enabled = previous
        db.commit()

        # Create subscription checkout for ALL enabled properties (including the one they tried to enable)
        checkout = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_property, "quantity": enabled_count}],
            success_url=f"{app_base_url}/pmc/onboarding/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{app_base_url}/admin/dashboard#properties",
            metadata={
                "pmc_id": str(pmc.id),
                "type": "pmc_property_subscription",
                "quantity": str(enabled_count),
            },
        )

        return JSONResponse({"status": "needs_billing", "checkout_url": checkout.url})

    # Subscription exists and is not fully canceled: sync quantity + cancel_at_period_end policy
    try:
        sync_property_quantity(db, pmc.id, proration_behavior="none")
    except Exception as e:
        prop.sandy_enabled = previous
        db.commit()
        raise HTTPException(status_code=500, detail=f"Billing update failed, change reverted: {str(e)}")

    return JSONResponse({"status": "success", "new_status": "LIVE"})



@router.post("/sync-property/{property_id}")
def sync_single_property(property_id: int, request: Request, db: Session = Depends(get_db)):
    prop = require_property_in_scope(request, db, property_id)

    # ✅ Paywall: PMC must be paid+active to sync
    require_pmc_is_paid(db, prop.pmc_id)

    pmc = db.query(PMC).filter(PMC.id == prop.pmc_id).first()
    if not pmc:
        raise HTTPException(status_code=404, detail="PMC not found")

    try:
        sync_properties(pmc.pms_account_id)
        return JSONResponse({"status": "success", "message": "Synced!"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)




@router.get("/logout")
def logout(request: Request):
    # Clear all auth + session state (admin + pmc users)
    request.session.clear()

    # Single entry point: dashboard will show login if not authenticated
    return RedirectResponse(url="/admin/dashboard", status_code=302)

