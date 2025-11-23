from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.config import Config
from authlib.integrations.starlette_client import OAuth
import os

from utils.airtable_client import get_pmcs_table

router = APIRouter(prefix="/auth")
templates = Jinja2Templates(directory="templates")

# Load from environment
config = Config(environ={
    "GOOGLE_CLIENT_ID": os.getenv("GOOGLE_CLIENT_ID"),
    "GOOGLE_CLIENT_SECRET": os.getenv("GOOGLE_CLIENT_SECRET")
})

# Register OAuth
oauth = OAuth(config)

oauth.register(
    name='google',
    client_id=config('GOOGLE_CLIENT_ID'),
    client_secret=config('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# ✅ Airtable check for authorized email
def is_pmc_email_valid(email: str) -> bool:
    table = get_pmcs_table()
    records = table.all()
    return any(record['fields'].get('Email') == email for record in records)

from utils.airtable_client import get_properties_table

def get_properties_for_pmc(email: str):
    table = get_properties_table()
    records = table.all()
    return [r for r in records if r['fields'].get('PMC Email') == email]

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


# 🔐 Login with Google
@router.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for('auth_callback')
    return await oauth.google.authorize_redirect(request, redirect_uri)

# 🎯 Callback from Google
@router.get("/callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        user = await oauth.google.parse_id_token(request, token)
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

# 🚪 Logout
@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")

@router.get("/login", response_class=HTMLResponse)
def show_login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

from authlib.integrations.starlette_client import OAuth

oauth = OAuth()
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

@router.get("/login/google")
async def login_with_google(request: Request):
    redirect_uri = request.url_for('auth_callback')
    return await oauth.google.authorize_redirect(request, redirect_uri)


# 👤 Check login status
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
