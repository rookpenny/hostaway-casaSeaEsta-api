from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import os
import requests
from utils.hostaway import sync_hostaway_properties

# Airtable settings
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"

# Template setup
templates = Jinja2Templates(directory="templates")

# Router setup
admin_router = APIRouter(prefix="/admin")

# 🔷 Admin dashboard
@admin_router.get("", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    return templates.TemplateResponse("admin_dashboard.html", {"request": request})

# 🔄 Manual sync endpoint (used by Sync button)
@admin_router.post("/sync-hostaway-properties")
def sync_hostaway_properties_route():
    try:
        result = sync_hostaway_properties()
        return {"success": True, "synced": result}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to sync Hostaway properties", "details": str(e)}
        )

# ➕ Show form to create new PMC
@admin_router.get("/new-pmc", response_class=HTMLResponse)
def show_new_pmc_form(request: Request):
    return templates.TemplateResponse("pmc_form.html", {"request": request})

# ✅ Handle PMC form submission
@admin_router.post("/add-pmc")
def add_pmc_to_airtable(
    pmc_name: str = Form(...),
    hostaway_account_id: str = Form(...),
    pmc_id: str = Form(...),
    contact_email: str = Form(...),
    main_contact: str = Form(...),
    subscription_plan: str = Form(...),
    active: bool = Form(False)
):
    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "fields": {
            "Name": pmc_name,
            "Hostaway Account ID": hostaway_account_id,
            "PMC ID": pmc_id,
            "Email": contact_email,
            "Main Contact": main_contact,
            "Subscription Plan": subscription_plan,
            "Active": active
        }
    }

    response = requests.post(airtable_url, headers=headers, json=payload)

    if response.status_code in (200, 201):
        return RedirectResponse(url="/admin", status_code=303)
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to add PMC", "details": response.text}
        )
