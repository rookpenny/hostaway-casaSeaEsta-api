from fastapi import APIRouter, Request, Form, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import os
import requests
import traceback
from utils.hostaway_sync import sync_hostaway_properties  # ✅ correct
from utils.scheduler import sync_all_pmcs

admin_router = APIRouter()



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

    debug_info = {
        "AIRTABLE_API_KEY": "✅ SET" if AIRTABLE_API_KEY else "❌ MISSING",
        "AIRTABLE_BASE_ID": AIRTABLE_BASE_ID or "❌ MISSING",
        "AIRTABLE_PMC_TABLE_ID": AIRTABLE_PMC_TABLE_ID
    }

    try:
        response = requests.get(airtable_url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            pmcs = data.get("records", [])
        else:
            debug_info["Airtable Response Code"] = response.status_code
            debug_info["Airtable Response"] = response.text
    except Exception as e:
        print(f"[ERROR] Failed to fetch PMCs: {e}")
        debug_info["Exception"] = str(e)

    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "pmcs": pmcs,
        "debug_info": debug_info,
        "status": request.query_params.get("status", "")
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
    pmcs = []
    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}"
    }

    try:
        response = requests.get(airtable_url, headers=headers)
        if response.status_code == 200:
            pmcs = response.json().get("records", [])
    except Exception as e:
        print(f"[ERROR] Failed to fetch PMCs for form: {e}")

    # Extract unique dropdown values
    def get_unique_field_values(records, field_name):
        return sorted({
            record.get("fields", {}).get(field_name)
            for record in records
            if record.get("fields", {}).get(field_name)
        })

    pms_integrations = get_unique_field_values(pmcs, "PMS Integration")
    subscription_plans = get_unique_field_values(pmcs, "Subscription Plan")

    return templates.TemplateResponse("pmc_form.html", {
        "request": request,
        "pms_integrations": pms_integrations,
        "subscription_plans": subscription_plans
    })



@admin_router.post("/sync-properties/{hostaway_account_id}")
def sync_properties_for_pmc(hostaway_account_id: str):
    try:
        from utils.hostaway_sync import sync_hostaway_properties
        sync_hostaway_properties(account_id=hostaway_account_id)
        return RedirectResponse(url="/admin", status_code=303)
    except Exception as e:
        print(f"[ERROR] Failed syncing for Hostaway Account ID {hostaway_account_id}: {e}")
        return RedirectResponse(url="/admin?status=error", status_code=303)


@admin_router.post("/update-status")
def update_pmc_status(payload: dict = Body(...)):
    record_id = payload.get("record_id")
    active = payload.get("active", False)

    if not record_id:
        return JSONResponse(status_code=400, content={"error": "Missing record_id"})

    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}/{record_id}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "fields": {
            "Active": active
        }
    }

    try:
        response = requests.patch(airtable_url, headers=headers, json=data)
        if response.status_code in (200, 201):
            return {"success": True}
        else:
            return JSONResponse(status_code=500, content={"error": "Failed to update", "details": response.text})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ✅ Handle form submission and create PMC in Airtable
@admin_router.post("/add-pmc")
def add_pmc_to_airtable(
    pmc_name: str = Form(...),
    contact_email: str = Form(...),
    main_contact: str = Form(...),
    subscription_plan: str = Form(...),
    pms_integration: str = Form(...),
    pms_client_id: str = Form(...),
    pms_secret: str = Form(...),
    active: bool = Form(False)
):
    airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "fields": {
            "PMC Name": pmc_name,
            "Email": contact_email,
            "Main Contact": main_contact,
            "Subscription Plan": subscription_plan,
            "PMS Integration": pms_integration,
            "PMS Client ID": pms_client_id,
            "PMS Secret": pms_secret,
            "Active": active
        }
    }

    try:
        response = requests.post(airtable_url, headers=headers, json=payload)
        response.raise_for_status()
        return RedirectResponse(url="/admin?status=success", status_code=303)
    except Exception as e:
        print("[ERROR] Failed to create PMC:", e)
        return RedirectResponse(url="/admin?status=error", status_code=303)

@admin_router.post("/admin/sync-all")
def manual_sync_all():
    try:
        sync_all_pmcs()
        return RedirectResponse(url="/admin?status=success", status_code=303)
    except Exception as e:
        print("[ERROR] Sync failed:", e)
        return RedirectResponse(url="/admin?status=error", status_code=303)
