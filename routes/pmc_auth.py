import os
from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from starlette.config import Config
from authlib.integrations.starlette_client import OAuth

from sqlalchemy.orm import Session
from sqlalchemy import func

from database import SessionLocal, get_db
from models import PMC, Property, PMCUser
from utils.pms_sync import sync_properties  # sync by account_id


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
async def login_with_google(request: Request):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return HTMLResponse(
            "<h2>OAuth not configured</h2><p>Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET</p>",
            status_code=500,
        )
    redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        userinfo = await oauth.google.userinfo(token=token)

        email = (userinfo.get("email") or "").strip()
        name = userinfo.get("name")

        if not email:
            return HTMLResponse(
                "<h2>Access denied: No email returned from Google</h2>",
                status_code=400
            )

        scope = resolve_login_scope(email)
        if not scope["ok"]:
            return HTMLResponse(
                f"<h2>Access denied: {scope['error']}</h2>",
                status_code=403
            )

        # ✅ base identity (admin.py reads session["user"]["email"] and/or session["admin_email"])
        email_l = email.lower()
        request.session["user"] = {"email": email_l, "name": name}
        request.session["admin_email"] = email_l  # matches ADMIN_IDENTITY_SESSION_KEY default

        # ✅ role + scope
        request.session["role"] = scope["role"]
        request.session["pmc_id"] = scope["pmc_id"]
        request.session["pmc_user_id"] = scope["pmc_user_id"]

        # ✅ Redirect back to where they originally tried to go (default: /admin/dashboard)
        next_url = request.session.get("post_login_redirect") or "/admin/dashboard"
        request.session.pop("post_login_redirect", None)

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
    prop.sandy_enabled = not bool(prop.sandy_enabled)
    db.commit()

    return JSONResponse({"status": "success", "new_status": "LIVE" if prop.sandy_enabled else "OFFLINE"})


@router.post("/sync-property/{property_id}")
def sync_single_property(property_id: int, request: Request, db: Session = Depends(get_db)):
    prop = require_property_in_scope(request, db, property_id)

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
    property_id = request.session.get("last_property")
    request.session.clear()

    if property_id:
        return RedirectResponse(url=f"/guest/{property_id}", status_code=302)

    return RedirectResponse(url="/", status_code=302)
