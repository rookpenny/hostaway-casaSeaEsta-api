from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import os
import requests
from utils.hostaway import sync_hostaway_properties

# Airtable settings
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"  # Replace with your actual table ID

# Template setup
templates = Jinja2Templates(directory="templates")

# Router setup
admin_router = APIRouter(prefix="/admin")


# 🔷 Admin dashboard (lists existing PMCs)
@admin_router.get("", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    pmcs = []
    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}"
    }

    try:
        response = requests.get(airtable_url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            pmcs = data.get("records", [])
    except Exception as e:
        print(f"[ERROR] Failed to fetch PMCs: {e}")

    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "pmcs": pmcs
    })


# 🔄 Manual sync endpoint for Hostaway properties
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


# ➕ Show the form to create a new PMC
@admin_router.get("/new-pmc", response_class=HTMLResponse)
def show_new_pmc_form(request: Request):
    return templates.TemplateResponse("pmc_form.html", {"request": request})


# ✅ Handle form submission and create PMC in Airtable
@admin_router.post("/add-pmc")
def add_pmc_to_airtable(
    pmc_name: str = Form(...),
    hostaway_account_id: str = Form(...),
    contact_email: str = Form(...),
    main_contact: str = Form(...),
    subscription_plan: str = Form(...),
    pms_integration: str = Form(...),
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
            "Email": contact_email,
            "Main Contact": main_contact,
            "Subscription Plan": subscription_plan,
            "PMS Integration": pms_integration,
            "Active": active
        }
    }

    try:
        response = requests.post(airtable_url, headers=headers, json=payload)
        response.raise_for_status()
        return RedirectResponse(url="/admin?status=success", status_code=303)
    except Exception as e:
        print(f"[ERROR] Failed to add PMC to Airtable: {e}")
        return RedirectResponse(url="/admin?status=error", status_code=303)
