# routes/pmc_auth.py
from __future__ import annotations

import os
import secrets
import smtplib
from email.mime.text import MIMEText
from typing import Optional, Dict, Any, List

from fastapi import (
    APIRouter,
    Request,
    Depends,
    HTTPException,
    BackgroundTasks,
    Form,
)
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from starlette.config import Config
from authlib.integrations.starlette_client import OAuth

from sqlalchemy.orm import Session
from sqlalchemy import func

from database import SessionLocal, get_db
from models import PMC, Property, PMCUser, PMCIntegration

from utils.pms_sync import sync_properties  # expects integration_id (new)
from utils.billing_guard import require_pmc_is_paid
from utils.billing import charge_property_for_month_if_needed

router = APIRouter(prefix="/auth")
templates = Jinja2Templates(directory="templates")


# ----------------------------
# OAuth Config
# ----------------------------
GOOGLE_CLIENT_ID = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
GOOGLE_CLIENT_SECRET = (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()

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
            .filter(func.lower(PMCUser.email) == email_l, PMCUser.is_active.is_(True))
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
            .filter(func.lower(PMC.email) == email_l, PMC.active.is_(True))
            .first()
        )
        if pmc:
            return {"ok": True, "role": "pmc", "pmc_id": pmc.id, "pmc_user_id": None, "error": None}

        return {"ok": False, "role": None, "pmc_id": None, "pmc_user_id": None, "error": "Unauthorized email"}
    finally:
        db.close()


# ----------------------------
# Magic email login (optional)
# ----------------------------
def send_magic_email(to: str, magic_url: str) -> None:
    subject = "Your HostScout login link"
    body = f"""Hi there,

We received a request to log in using this email address.

Click the link below to sign in:

{magic_url}

If you did not request this email, you can safely ignore it.

— HostScout
"""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = os.getenv("SMTP_FROM", "no-reply@hostscout.app")
    msg["To"] = to

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")

    if not all([smtp_host, smtp_user, smtp_pass]):
        print(f"[Email mock] To: {to}, URL: {magic_url}")
        return

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(msg["From"], [to], msg.as_string())


@router.post("/login/email")
async def login_with_email(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    email_l = (email or "").strip().lower()
    if not email_l:
        return HTMLResponse("<h2>Invalid email address.</h2>", status_code=400)

    scope = resolve_login_scope(email_l)
    if not scope["ok"]:
        return HTMLResponse(
            "<h2>We couldn’t find that email. Please check and try again.</h2>",
            status_code=403,
        )

    token = secrets.token_urlsafe(32)
    request.session["email_login_token"] = token
    request.session["email_login_target"] = email_l

    app_base = (os.getenv("APP_BASE_URL") or "").rstrip("/")
    magic_url = f"{app_base}/auth/email-callback?token={token}"

    background_tasks.add_task(send_magic_email, to=email_l, magic_url=magic_url)

    return templates.TemplateResponse("login_email_sent.html", {"request": request, "email": email_l})


@router.get("/email-callback")
def email_callback(token: str, request: Request, db: Session = Depends(get_db)):
    stored_token = request.session.get("email_login_token")
    target_email = request.session.get("email_login_target")

    if not stored_token or token != stored_token or not target_email:
        return HTMLResponse("<h2>Invalid or expired login link.</h2>", status_code=403)

    scope = resolve_login_scope(target_email)
    if not scope["ok"]:
        return HTMLResponse("<h2>Unauthorized email.</h2>", status_code=403)

    # One-time token
    request.session.pop("email_login_token", None)
    request.session.pop("email_login_target", None)

    request.session["user"] = {"email": target_email, "name": None}
    request.session["admin_email"] = target_email
    request.session["role"] = scope["role"]
    request.session["pmc_id"] = scope["pmc_id"]
    request.session["pmc_user_id"] = scope["pmc_user_id"]

    return RedirectResponse("/admin/dashboard", status_code=302)


# ----------------------------
# Auth pages
# ----------------------------
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return HTMLResponse(
            "<h2>OAuth not configured</h2><p>Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET</p>",
            status_code=500,
        )
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/login/google")
async def login_with_google(request: Request, next: str = "/admin/dashboard"):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return HTMLResponse(
            "<h2>OAuth not configured</h2><p>Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET</p>",
            status_code=500,
        )

    # Only allow safe internal redirects
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

    - Authorized Super/PMC -> normal session and redirect.
    - Unauthorized but headed to /pmc/signup -> allow limited 'signup' role session.
    - Otherwise -> access_denied template.
    """
    try:
        token = await oauth.google.authorize_access_token(request)
        userinfo = await oauth.google.userinfo(token=token)

        email = (userinfo.get("email") or "").strip()
        name = (userinfo.get("name") or "").strip() or None

        if not email:
            return HTMLResponse("<h2>Access denied: No email returned from Google</h2>", status_code=400)

        email_l = email.lower()
        next_url = request.session.get("post_login_redirect") or "/admin/dashboard"
        request.session.pop("post_login_redirect", None)

        scope = resolve_login_scope(email_l)

        if not scope["ok"]:
            if (next_url or "").startswith("/pmc/signup"):
                request.session["user"] = {"email": email_l, "name": name}
                request.session["admin_email"] = email_l
                request.session["role"] = "signup"
                request.session["pmc_id"] = None
                request.session["pmc_user_id"] = None
                return RedirectResponse(url=next_url, status_code=302)

            return templates.TemplateResponse(
                "access_denied.html",
                {"request": request, "error": scope.get("error") or "Unauthorized email", "email": email_l},
                status_code=403,
            )

        request.session["user"] = {"email": email_l, "name": name}
        request.session["admin_email"] = email_l
        request.session["role"] = scope["role"]  # super | pmc
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


# ----------------------------
# Scope helpers (auth-side)
# ----------------------------
def require_property_in_scope(request: Request, db: Session, property_id: int) -> Property:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=403, detail="Unauthorized")

    role = request.session.get("role")
    pmc_id = request.session.get("pmc_id")

    prop = db.query(Property).filter(Property.id == int(property_id)).first()
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


# ----------------------------
# Property actions
# ----------------------------
@router.post("/toggle-property/{property_id}")
def toggle_property(property_id: int, request: Request, db: Session = Depends(get_db)):
    prop = require_property_in_scope(request, db, property_id)

    # Paywall: must have paid setup / be active for admin controls
    require_pmc_is_paid(db, prop.pmc_id)

    pmc = db.query(PMC).filter(PMC.id == prop.pmc_id).first()
    if not pmc:
        raise HTTPException(status_code=404, detail="PMC not found")

    previous = bool(prop.sandy_enabled)
    new_value = not previous

    # ---- Turning OFF ----
    if new_value is False:
        try:
            prop.sandy_enabled = False
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Toggle failed: {str(e)}")

        return JSONResponse(
            {
                "status": "success",
                "new_status": "OFFLINE",
                "billing_note": "Offline now. You won’t be charged again unless you turn it back on in a new month.",
            }
        )

    # ---- Turning ON (bill-on-enable) ----
    try:
        # Make the enable + charge atomic
        prop.sandy_enabled = True

        charged = charge_property_for_month_if_needed(db, pmc, prop)

        db.commit()
        db.refresh(prop)

    except Exception as e:
        db.rollback()

        # Ensure property is not left live if billing fails
        try:
            prop.sandy_enabled = previous
            db.commit()
        except Exception:
            db.rollback()

        raise HTTPException(status_code=500, detail=f"Billing failed, change reverted: {str(e)}")

    note = (
        "Live now. Charged for this month."
        if charged
        else "Live now. Already covered for this month (no additional charge)."
    )

    return JSONResponse({"status": "success", "new_status": "LIVE", "billing_note": note})

@router.post("/sync-property/{property_id}")
def sync_single_property(property_id: int, request: Request, db: Session = Depends(get_db)):
    prop = require_property_in_scope(request, db, property_id)

    # Paywall: PMC must be paid+active to sync
    require_pmc_is_paid(db, prop.pmc_id)

    # Prefer integration_id from the property (new schema)
    integration_id = getattr(prop, "integration_id", None)

    # Fallback: find the integration for this PMC/provider
    if not integration_id:
        provider = (getattr(prop, "provider", None) or "").strip().lower()
        if provider:
            integ = (
                db.query(PMCIntegration)
                .filter(PMCIntegration.pmc_id == prop.pmc_id, PMCIntegration.provider == provider)
                .first()
            )
            if integ:
                integration_id = integ.id

    if not integration_id:
        # final fallback: legacy pmc.pms_account_id (old schema)
        pmc = db.query(PMC).filter(PMC.id == prop.pmc_id).first()
        if not pmc or not getattr(pmc, "pms_account_id", None):
            raise HTTPException(status_code=500, detail="No integration found for this property")
        # NOTE: only works if your sync_properties still supports legacy identifiers
        try:
            sync_properties(int(pmc.pms_account_id))  # legacy misuse, but keeps backward compat if you still allow it
            return JSONResponse({"status": "success", "message": "Synced!"})
        except Exception as e:
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    try:
        sync_properties(int(integration_id))
        return JSONResponse({"status": "success", "message": "Synced!"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# ----------------------------
# Logout
# ----------------------------
@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/dashboard", status_code=302)
