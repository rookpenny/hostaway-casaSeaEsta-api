# routes/pmc_auth.py

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import os
import requests
import hashlib

router = APIRouter(prefix="/auth")
templates = Jinja2Templates(directory="templates")

# Environment variables
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"  # ✅ Confirm correct

def hash_password(password: str) -> str:
    """Returns a SHA-256 hashed password"""
    return hashlib.sha256(password.encode()).hexdigest()

# ➕ Render login form
@router.get("/login", response_class=HTMLResponse)
def show_login_form(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": request.query_params.get("error", "")
    })

# ✅ Process login form
@router.post("/login")
def process_login(request: Request, email: str = Form(...), password: str = Form(...)):
    hashed_pw = hash_password(password)

    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}
    params = {"filterByFormula": f"{{Email}} = '{email}'"}

    response = requests.get(airtable_url, headers=headers, params=params)

    if response.status_code == 200:
        records = response.json().get("records", [])
        if records:
            stored_hash = records[0]["fields"].get("Password")
            if stored_hash and stored_hash == hashed_pw:
                # TODO: Set secure session here (JWT, cookie, etc.)
                return RedirectResponse(url="/dashboard", status_code=303)

    # ❌ Failed login
    return RedirectResponse(url="/auth/login?error=Invalid credentials", status_code=303)
