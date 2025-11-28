from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.config import Config
from authlib.integrations.starlette_client import OAuth
import os
from datetime import datetime
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from models import PMC, Property
from utils.pms_sync import sync_properties  # ⬅️ used in sync_single_property

#from utils.airtable_client import get_pmcs_table, get_properties_table

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
def is_pmc_email_valid(email: str) -> bool:
    """
    Returns True if there is an ACTIVE PMC with this email.
    Adjust later if you add a separate PMC user table.
    """
    db = SessionLocal()
    try:
        return db.query(PMC).filter(PMC.email == email, PMC.active == True).first() is not None
    finally:
        db.close()


@router.post("/toggle-property/{property_id}")
def toggle_property(
    request: Request,
    property_id: int,
    db: Session = Depends(get_db)
):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=403, detail="Unauthorized")

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    pmc = db.query(PMC).filter(PMC.id == prop.pmc_id, PMC.email == user["email"]).first()
    if not pmc:
        raise HTTPException(status_code=403, detail="Unauthorized")

    # Flip the status
    prop.sandy_enabled = not prop.sandy_enabled
    db.commit()

    return JSONResponse({
        "status": "success",
        "new_status": "LIVE" if prop.sandy_enabled else "OFFLINE"
    })



@router.post("/sync-property/{property_id}")
def sync_single_property(request: Request, property_id: int, db: Session = Depends(get_db)):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=403, detail="Unauthorized")

    # Fetch property by ID and check ownership
    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        return JSONResponse({"status": "error", "message": "Property not found"}, status_code=404)

    pmc = db.query(PMC).filter(PMC.id == prop.pmc_id, PMC.email == user["email"]).first()
    if not pmc:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    try:
        sync_properties(pmc.pms_account_id)  # already defined
        return JSONResponse({"status": "success", "message": "Synced!"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# --- Fetch Properties for This PMC ---
def get_properties_for_pmc(email: str):
    db: Session = SessionLocal()
    pmc = db.query(PMC).filter(PMC.email == email).first()
    if not pmc:
        return []

    # Properties are accessed via relationship
    return pmc.properties



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

        if not email:
            return HTMLResponse("<h2>Access denied: No email returned from Google</h2>", status_code=400)

        if not is_pmc_email_valid(email):
            return HTMLResponse("<h2>Access denied: Unauthorized email</h2>", status_code=403)

        request.session['user'] = {
            "email": email,
            "name": user.get("name")
        }

        return RedirectResponse(url="/auth/dashboard")

    except Exception as e:
        print("[OAuth Error]", e)
        return HTMLResponse(f"<h2>OAuth Error: {e}</h2>", status_code=500)



@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/auth/login")

    # 🔍 Find PMC record in the database using email
    pmc = db.query(PMC).filter(PMC.email == user["email"]).first()
    if not pmc:
        return HTMLResponse("<h2>No PMC found for this email</h2>", status_code=404)

    properties = pmc.properties

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "properties": properties,
        "now": datetime.utcnow()
    })


# --- Logout
@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")
