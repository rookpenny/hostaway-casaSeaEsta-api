from fastapi import APIRouter, Request, Form, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import os
import requests
from utils.pms_sync import sync_properties, sync_all_pmcs

admin_router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")

# Airtable Settings
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"  # PMC table ID

# 🧭 Admin Dashboard
@admin_router.get("", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    pmcs = []
    debug_info = {
        "AIRTABLE_API_KEY": "✅ SET" if AIRTABLE_API_KEY else "❌ MISSING",
        "AIRTABLE_BASE_ID": AIRTABLE_BASE_ID or "❌ MISSING",
        "AIRTABLE_PMC_TABLE_ID": AIRTABLE_PMC_TABLE_ID
    }

    try:
        airtable_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
        headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}
        response = requests.get(airtable_url, headers=headers)

        if response.status_code == 200:
            pmcs = response.json().get("records", [])
        else:
            debug_info["Airtable Response Code"] = response.status_code
            debug_info["Airtable Response"] = response.text
    except Exception as e:
        debug_info["Exception"] = str(e)

    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "pmcs": pmcs,
        "debug_info": debug_info,
        "status": request.query_params.get("status", "")
    })


# ➕ Show New PMC Form
@admin_router.get("/new-pmc", response_class=HTMLResponse)
def show_new_pmc_form(request: Request):
    pms_integrations = ["Hostaway", "Guesty", "Lodgify", "Other"]
    subscription_plans = ["Free", "Pro", "Enterprise"]

    return templates.TemplateResponse("pmc_form.html", {
        "request": request,
        "pms_integrations": pms_integrations,
        "subscription_plans": subscription_plans
    })


# ✅ Add a New PMC
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
    # Airtable endpoint
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }

    # Step 1: Fetch all current PMS Account IDs
    try:
        existing_ids = []
        offset = None
        while True:
            params = {"fields[]": ["PMS Account ID"]}
            if offset:
                params["offset"] = offset

            res = requests.get(url, headers=headers, params=params)
            res.raise_for_status()
            records = res.json().get("records", [])

            for rec in records:
                fields = rec.get("fields", {})
                account_id = fields.get("PMS Account ID")
                if account_id:
                    try:
                        existing_ids.append(int(account_id))
                    except:
                        pass

            offset = res.json().get("offset")
            if not offset:
                break

        # Step 2: Determine the next available account ID
        next_account_id = max(existing_ids, default=1000) + 1

    except Exception as e:
        print("[ERROR] Failed to determine next account ID:", e)
        return RedirectResponse(url="/admin?status=error", status_code=303)

    # Step 3: Build payload with auto-generated PMS Account ID
    payload = {
        "fields": {
            "PMC Name": pmc_name,
            "Email": contact_email,
            "Main Contact": main_contact,
            "Subscription Plan": subscription_plan,
            "PMS Integration": pms_integration,
            "PMS Client ID": pms_client_id,
            "PMS Secret": pms_secret,
            "PMS Account ID": str(next_account_id),
            "Active": active,
            "Sync Enabled": True
        }
    }

    # Step 4: Send to Airtable
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return RedirectResponse(url="/admin?status=success", status_code=303)
    except Exception as e:
        print("[ERROR] Failed to create PMC:", e)
        return RedirectResponse(url="/admin?status=error", status_code=303)


# 🔁 Sync All PMCs
@admin_router.post("/sync-all")
def manual_sync_all():
    try:
        sync_all_pmcs()
        return RedirectResponse(url="/admin?status=success", status_code=303)
    except Exception as e:
        print("[ERROR] Sync failed:", e)
        return RedirectResponse(url="/admin?status=error", status_code=303)


# 🔁 Sync One PMC by Account ID
@admin_router.post("/sync-properties/{account_id}")
def sync_properties_for_pmc(account_id: str):
    try:
        synced = sync_properties(account_id=account_id)
        return RedirectResponse(url="/admin?status=success", status_code=303)
    except Exception as e:
        print(f"[ERROR] Failed syncing for Account ID {account_id}: {e}")
        return RedirectResponse(url="/admin?status=error", status_code=303)


# ✅ Toggle PMC Active Status
@admin_router.post("/update-status")
def update_pmc_status(payload: dict = Body(...)):
    record_id = payload.get("record_id")
    active = payload.get("active", False)

    if not record_id:
        return JSONResponse(status_code=400, content={"error": "Missing record_id"})

    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}/{record_id}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {"fields": {"Active": active}}

    try:
        response = requests.patch(url, headers=headers, json=data)
        if response.status_code in (200, 201):
            return {"success": True}
        else:
            return JSONResponse(status_code=500, content={"error": response.text})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
