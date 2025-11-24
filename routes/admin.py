from fastapi import APIRouter, Request, Form, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.status import HTTP_303_SEE_OTHER
import os
import requests
import json
from utils.pms_sync import sync_properties, sync_all_pmcs
from pathlib import Path
import openai

admin_router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")

# Airtable Settings
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"
openai.api_key = os.getenv("OPENAI_API_KEY")

client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@admin_router.get("/edit-config", response_class=HTMLResponse)
@admin_router.get("/edit-housemanual", response_class=HTMLResponse)
def edit_file(request: Request, file: str):
    try:
        file_path = Path(file)
        if not file_path.exists():
            return HTMLResponse(f"<h2>File not found: {file}</h2>", status_code=404)

        content = file_path.read_text(encoding='utf-8')

        return templates.TemplateResponse("editor.html", {
            "request": request,
            "file_path": file,
            "content": content
        })
    except Exception as e:
        return HTMLResponse(f"<h2>Error reading file: {e}</h2>", status_code=500)


@admin_router.post("/admin/save-file")
def save_file(file_path: str = Form(...), content: str = Form(...)):
    try:
        path = Path(file_path)
        path.write_text(content, encoding='utf-8')
        return HTMLResponse(f"<h2>File saved successfully.</h2><a href='/admin'>Back to Admin</a>")
    except Exception as e:
        return HTMLResponse(f"<h2>Failed to save file: {e}</h2>", status_code=500)

@admin_router.post("/admin/save-manual")
def save_manual_file(file_path: str = Form(...), content: str = Form(...)):
    import base64

    try:
        repo_owner = "rookpenny"
        repo_name = "hostscout_data"
        github_token = os.getenv("GITHUB_TOKEN")
        github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}"

        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        }

        # 🔍 Get current file SHA (required by GitHub API to update)
        get_response = requests.get(github_api_url, headers=headers)
        if get_response.status_code != 200:
            return HTMLResponse(f"<h2>GitHub Fetch Error: {get_response.status_code}<br>{get_response.text}</h2>", status_code=404)

        sha = get_response.json()["sha"]

        # 📝 Prepare payload for update
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        commit_message = f"Update manual file: {file_path}"

        payload = {
            "message": commit_message,
            "content": encoded_content,
            "sha": sha
        }

        put_response = requests.put(github_api_url, headers=headers, json=payload)

        if put_response.status_code in (200, 201):
            return HTMLResponse(f"<h2>Manual saved to GitHub successfully.</h2><a href='/auth/dashboard'>Return to Dashboard</a>")
        else:
            return HTMLResponse(f"<h2>GitHub Save Error: {put_response.status_code}<br>{put_response.text}</h2>", status_code=500)

    except Exception as e:
        return HTMLResponse(f"<h2>Exception while saving: {e}</h2>", status_code=500)



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

# Helper to determine next PMS Account ID
def get_next_pms_account_id():
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PMC_TABLE_ID}"
    params = {
        "sort[0][field]": "PMS Account ID",
        "sort[0][direction]": "desc",
        "maxRecords": 1
    }
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    records = response.json().get("records", [])

    if records:
        last_id = int(records[0]["fields"].get("PMS Account ID", 10000))
        return last_id + 1
    return 10000

# ✅ Add New PMC (no password)
@admin_router.post("/add-pmc")
async def add_pmc(
    pmc_name: str = Form(...),
    contact_email: str = Form(...),
    main_contact: str = Form(...),
    subscription_plan: str = Form(...),
    pms_integration: str = Form(...),
    pms_client_id: str = Form(...),
    pms_secret: str = Form(...),
    active: bool = Form(False)
):
    print("[DEBUG] Received POST /admin/add-pmc")
    try:
        new_account_id = get_next_pms_account_id()
        print(f"[DEBUG] Next PMS Account ID: {new_account_id}")

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
                "PMS Account ID": new_account_id,
                "Active": active,
                "Sync Enabled": active
            }
        }

        print("[DEBUG] Airtable POST URL:", airtable_url)
        print("[DEBUG] Payload to Airtable:\n", json.dumps(payload, indent=2))

        res = requests.post(airtable_url, json=payload, headers=headers)

        if res.status_code not in (200, 201):
            print(f"[ERROR] Failed to create PMC: {res.status_code} - {res.reason}")
            print(f"[DEBUG] Airtable response body: {res.text}")
            return RedirectResponse(url="/admin?status=error", status_code=303)

        print("[DEBUG] Airtable success response:", res.json())
        return RedirectResponse(url="/admin?status=success", status_code=303)

    except Exception as e:
        print(f"[ERROR] Exception while creating PMC: {e}")
        return RedirectResponse(url="/admin?status=error", status_code=303)


import base64

@admin_router.get("/edit-manual", response_class=HTMLResponse)
def edit_manual_file(request: Request, file: str):
    try:
        # Convert local-style path to GitHub path
        repo_owner = "rookpenny"
        repo_name = "hostscout_data"
        github_token = os.getenv("GITHUB_TOKEN")
        github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file}"

        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        }

        response = requests.get(github_api_url, headers=headers)
        if response.status_code != 200:
            return HTMLResponse(f"<h2>GitHub Error: {response.status_code}<br>{response.text}</h2>", status_code=404)

        data = response.json()
        content = base64.b64decode(data['content']).decode('utf-8')

        return templates.TemplateResponse("editor.html", {
            "request": request,
            "file_path": file,
            "content": content
        })
    except Exception as e:
        return HTMLResponse(f"<h2>Error loading file: {e}</h2>", status_code=500)



# 🔁 Sync All PMCs
@admin_router.post("/sync-all")
def manual_sync_all():
    try:
        sync_all_pmcs()
        return RedirectResponse(url="/admin?status=success", status_code=303)
    except Exception as e:
        print("[ERROR] Sync failed:", e)
        return RedirectResponse(url="/admin?status=error", status_code=303)

# 🔁 Sync Properties for One PMC
@admin_router.post("/sync-properties/{account_id}")
def sync_properties_for_pmc(account_id: str):
    try:
        synced = sync_properties(account_id=account_id)
        return RedirectResponse(url="/admin?status=success", status_code=303)
    except Exception as e:
        print(f"[ERROR] Failed syncing for Account ID {account_id}: {e}")
        return RedirectResponse(url="/admin?status=error", status_code=303)

@admin_router.post("/admin/save-config")
def save_config_file(file_path: str = Form(...), content: str = Form(...)):
    import base64

    try:
        repo_owner = "rookpenny"
        repo_name = "hostscout_data"
        github_token = os.getenv("GITHUB_TOKEN")
        github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}"

        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        }

        # 🔍 Get current file SHA
        get_response = requests.get(github_api_url, headers=headers)
        if get_response.status_code != 200:
            return HTMLResponse(f"<h2>GitHub Fetch Error: {get_response.status_code}<br>{get_response.text}</h2>", status_code=404)

        sha = get_response.json()["sha"]

        # 🔄 Encode updated content
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        commit_message = f"Update config file: {file_path}"

        payload = {
            "message": commit_message,
            "content": encoded_content,
            "sha": sha
        }

        put_response = requests.put(github_api_url, headers=headers, json=payload)

        if put_response.status_code in (200, 201):
            return HTMLResponse(f"<h2>Config saved to GitHub successfully.</h2><a href='/auth/dashboard'>Return to Dashboard</a>")
        else:
            return HTMLResponse(f"<h2>GitHub Save Error: {put_response.status_code}<br>{put_response.text}</h2>", status_code=500)

    except Exception as e:
        return HTMLResponse(f"<h2>Exception while saving: {e}</h2>", status_code=500)


@admin_router.get("/edit-config", response_class=HTMLResponse)
def edit_config_file(request: Request, file: str):
    import base64

    try:
        repo_owner = "rookpenny"
        repo_name = "hostscout_data"
        github_token = os.getenv("GITHUB_TOKEN")
        github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file}"

        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        }

        response = requests.get(github_api_url, headers=headers)
        if response.status_code != 200:
            return HTMLResponse(f"<h2>GitHub Error: {response.status_code}<br>{response.text}</h2>", status_code=404)

        data = response.json()
        content = base64.b64decode(data['content']).decode('utf-8')

        return templates.TemplateResponse("editor.html", {
            "request": request,
            "file_path": file,
            "content": content
        })
    except Exception as e:
        return HTMLResponse(f"<h2>Error loading config file: {e}</h2>", status_code=500)

@admin_router.get("/edit-file", response_class=HTMLResponse)
def edit_file_from_github(request: Request, file: str):
    import base64

    try:
        repo_owner = "rookpenny"
        repo_name = "hostscout_data"
        github_token = os.getenv("GITHUB_TOKEN")
        github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file}"

        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        }

        response = requests.get(github_api_url, headers=headers)
        if response.status_code != 200:
            return HTMLResponse(f"<h2>GitHub Error: {response.status_code}<br>{response.text}</h2>", status_code=404)

        data = response.json()
        content = base64.b64decode(data['content']).decode('utf-8')

        return templates.TemplateResponse("editor.html", {
            "request": request,
            "file_path": file,
            "content": content
        })
    except Exception as e:
        return HTMLResponse(f"<h2>Error loading file: {e}</h2>", status_code=500)

@admin_router.post("/save-github-file")
def save_github_file(file_path: str = Form(...), content: str = Form(...)):
    import base64

    try:
        repo_owner = "rookpenny"
        repo_name = "hostscout_data"
        github_token = os.getenv("GITHUB_TOKEN")
        github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}"

        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        }

        # Get current SHA of the file
        get_response = requests.get(github_api_url, headers=headers)
        if get_response.status_code != 200:
            return HTMLResponse(f"<h2>GitHub Fetch Error: {get_response.status_code}<br>{get_response.text}</h2>", status_code=404)

        sha = get_response.json()["sha"]

        # Encode the updated content
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        commit_message = f"Update {file_path}"

        payload = {
            "message": commit_message,
            "content": encoded_content,
            "sha": sha
        }

        put_response = requests.put(github_api_url, headers=headers, json=payload)

        if put_response.status_code in (200, 201):
            return RedirectResponse(url="/auth/dashboard?status=success", status_code=303)
            #return HTMLResponse(f"<h2>File saved to GitHub successfully.</h2><a href='/auth/dashboard'>Return to Dashboard</a>")
        else:
            return RedirectResponse(url="/auth/dashboard?status=success", status_code=303)
            #return HTMLResponse(f"<h2>GitHub Save Error: {put_response.status_code}<br>{put_response.text}</h2>", status_code=500)

    except Exception as e:
        return HTMLResponse(f"<h2>Exception while saving: {e}</h2>", status_code=500)

@admin_router.post("/admin/save-github-file")
def save_github_file(file_path: str = Form(...), content: str = Form(...)):
    import base64

    try:
        repo_owner = "rookpenny"
        repo_name = "hostscout_data"
        github_token = os.getenv("GITHUB_TOKEN")
        github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}"

        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        }

        # 🔍 Get current file SHA
        get_response = requests.get(github_api_url, headers=headers)
        if get_response.status_code != 200:
            return HTMLResponse(f"<h2>GitHub Fetch Error: {get_response.status_code}<br>{get_response.text}</h2>", status_code=404)

        sha = get_response.json()["sha"]

        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        commit_message = f"Update file: {file_path}"

        payload = {
            "message": commit_message,
            "content": encoded_content,
            "sha": sha
        }

        put_response = requests.put(github_api_url, headers=headers, json=payload)

        if put_response.status_code in (200, 201):
            return HTMLResponse(f"<h2>File saved to GitHub successfully.</h2><a href='/auth/dashboard'>Return to Dashboard</a>")
        else:
            return HTMLResponse(f"<h2>GitHub Save Error: {put_response.status_code}<br>{put_response.text}</h2>", status_code=500)

    except Exception as e:
        return HTMLResponse(f"<h2>Exception while saving: {e}</h2>", status_code=500)

@admin_router.get("/chat-ui", response_class=HTMLResponse)
def chat_ui(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})

#@admin_router.get("/chat", response_class=HTMLResponse)
#def chat_page(request: Request):
#    return templates.TemplateResponse("chat.html", {"request": request})

# ✅ Serve the chat interface HTML
#@admin_router.get("/chat", response_class=HTMLResponse)
#def chat_interface(request: Request):
#    return templates.TemplateResponse("chat.html", {"request": request})

# ✅ POST endpoint that receives a user message and sends it to ChatGPT
#@admin_router.post("/chat")
#async def chat_api(payload: dict):
#    user_message = payload.get("message", "")
 #   if not user_message:
 #       return {"reply": "Please say something!"}

 #   try:
  #      response = client.chat.completions.create(
 #           model="gpt-4",
#            messages=[
#                {"role": "system", "content": "You are Sandy, a helpful and funny assistant."},
#                {"role": "user", "content": user_message}
#            ]
#        )
#        reply = response.choices[0].message.content
#        return {"reply": reply}

#    except Exception as e:
#        return {"reply": f"❌ Error contacting ChatGPT: {e}"}

#@admin_router.get("/chat", response_class=HTMLResponse)
#def chat_ui(request: Request):
#    return templates.TemplateResponse("chat.html", {"request": request})

@admin_router.api_route("/chat", methods=["GET", "POST"])
async def chat_combined(request: Request):
    if request.method == "GET":
        return templates.TemplateResponse("chat.html", {"request": request})
    else:
        data = await request.json()
        user_message = data.get("message", "")
        if not user_message:
            return {"reply": "Please say something!"}

        #import openai
        openai.api_key = os.getenv("OPENAI_API_KEY")

        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are Sandy, a helpful and funny assistant."},
                {"role": "user", "content": user_message}
            ]
        )
        return {"reply": response.choices[0].message["content"]}


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
