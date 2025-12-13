from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.config import Config
from authlib.integrations.starlette_client import OAuth
import os
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import SessionLocal, get_db
from models import PMC, Property, PMCUser  # ✅ add PMCUser
from utils.pms_sync import sync_properties  # ⬅️ used in sync_single_property

router = APIRouter(prefix="/auth")
templates = Jinja2Templates(directory="templates")

# --- OAuth Config ---
config = Config(environ={
    "GOOGLE_CLIENT_ID": os.getenv("GOOGLE_CLIENT_ID"),
    "GOOGLE_CLIENT_SECRET": os.getenv("GOOGLE_CLIENT_SECRET")
})

oauth = OAuth(config)
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile',
        'response_type': 'code'
    }
)

# --- Email Authorization Check (Postgres-based) ---
def is_super_admin_email(email: str) -> bool:
    allow = os.getenv("ADMIN_EMAILS", "")
    if not allow.strip():
        return False
    allowed = {e.strip().lower() for e in allow.split(",") if e.strip()}
    return email.strip().lower() in allowed


def resolve_login_scope(email: str):
    """
    Returns dict:
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

    # 1) Super admins from env allowlist
    if is_super_admin_email(email_l):
        return {"ok": True, "role": "super", "pmc_id": None, "pmc_user_id": None, "error": None}

    db = SessionLocal()
    try:
        # 2) PMC team membership (preferred)
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
                    "error": None
                }
            return {"ok": False, "role": None, "pmc_id": None, "pmc_user_id": None, "error": "PMC is inactive"}

        # 3) Optional fallback: PMC owner email on PMC table
        pmc = db.query(PMC).filter(func.lower(PMC.email) == email_l, PMC.active == True).first()
        if pmc:
            return {"ok": True, "role": "pmc", "pmc_id": pmc.id, "pmc_user_id": None, "error": None}

        return {"ok": False, "role": None, "pmc_id": None, "pmc_user_id": None, "error": "Unauthorized email"}
    finally:
        db.close()


@router.post("/toggle-property/{property_id}")
def toggle_property(request: Request, property_id: int, db: Session = Depends(get_db)):
    prop = require_property_in_scope(request, db, property_id)

    prop.sandy_enabled = not prop.sandy_enabled
    db.commit()

    return JSONResponse({
        "status": "success",
        "new_status": "LIVE" if prop.sandy_enabled else "OFFLINE"
    })




@router.post("/sync-property/{property_id}")
def sync_single_property(request: Request, property_id: int, db: Session = Depends(get_db)):
    prop = require_property_in_scope(request, db, property_id)

    pmc = db.query(PMC).filter(PMC.id == prop.pmc_id).first()
    if not pmc:
        raise HTTPException(status_code=404, detail="PMC not found")

    try:
        sync_properties(pmc.pms_account_id)
        return JSONResponse({"status": "success", "message": "Synced!"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# --- Fetch Properties for This PMC ---
def get_properties_for_pmc(email: str):
    db: Session = SessionLocal()
    try:
        pmc = db.query(PMC).filter(PMC.email == email).first()
        if not pmc:
            return []
        return pmc.properties
    finally:
        db.close()


# --- Login Page (manual access)
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

# --- Login with Google
@router.get("/login/google")
async def login_with_google(request: Request):
    redirect_uri = request.url_for('auth_callback')
    return await oauth.google.authorize_redirect(request, redirect_uri)

# --- Google Callback
@router.get("/callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)

        user = await oauth.google.userinfo(token=token)
        email = user.get("email")
        name = user.get("name")

        if not email:
            return HTMLResponse("<h2>Access denied: No email returned from Google</h2>", status_code=400)

        scope = resolve_login_scope(email)
        if not scope["ok"]:
            return HTMLResponse(f"<h2>Access denied: {scope['error']}</h2>", status_code=403)

        # ✅ base identity
        request.session["user"] = {
            "email": email.strip().lower(),
            "name": name,
        }
        request.session["admin_email"] = email.strip().lower()

        # ✅ role + scope
        request.session["role"] = scope["role"]
        request.session["pmc_id"] = scope["pmc_id"]
        request.session["pmc_user_id"] = scope["pmc_user_id"]

        # ✅ send everyone to the unified admin dashboard
        return RedirectResponse(url="/admin/dashboard", status_code=302)

    except Exception as e:
        print("[OAuth Error]", e)
        return HTMLResponse(f"<h2>OAuth Error: {e}</h2>", status_code=500)


@router.get("/dashboard")
def dashboard(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/auth/login")

    return RedirectResponse(url="/admin/dashboard", status_code=302)

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


# --- Logout
@router.get("/logout")
def logout(request: Request):
    # Grab the last property before clearing the session
    property_id = request.session.get("last_property")

    # This clears guest_verified_{property_id} and everything else
    request.session.clear()

    if property_id:
        # 🔁 Send them back to the guest login screen for that property
        return RedirectResponse(url=f"/guest/{property_id}")

    # Fallback: no property found, go to a neutral page
    return RedirectResponse(url="/")
