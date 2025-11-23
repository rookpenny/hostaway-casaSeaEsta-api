from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.config import Config
from authlib.integrations.starlette_client import OAuth
import os

from utils.airtable_client import get_pmcs_table, get_properties_table

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

# --- Email Authorization Check ---
def is_pmc_email_valid(email: str) -> bool:
    table = get_pmcs_table()
    records = table.all()
    return any(record['fields'].get('Email') == email for record in records)

# --- Fetch Properties for This PMC ---
def get_properties_for_pmc(email: str):
    table = get_properties_table()
    records = table.all()
    return [r for r in records if r['fields'].get('PMC Email') == email]

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

        # SAFER: Use userinfo endpoint instead of id_token
        user = await oauth.google.userinfo(token=token)
        email = user.get("email")

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


# --- Dashboard
@router.get("/dashboard")
def dashboard(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/auth/login")

    properties = get_properties_for_pmc(user["email"])
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "properties": properties
    })

# --- Logout
@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")
