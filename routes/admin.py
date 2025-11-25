from fastapi import APIRouter, Request, Form, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.status import HTTP_303_SEE_OTHER
from sqlalchemy.orm import Session

import os
import requests
import json
from pathlib import Path

from utils.pms_sync import sync_properties, sync_all_pmcs
from database import SessionLocal
from models import PMC
from openai import OpenAI  # ✅ Updated OpenAI import


# 🚏 Router & Templates
router = APIRouter()
templates = Jinja2Templates(directory="templates")


# 🤖 OpenAI Client Setup
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))



# 📝 Edit Local Config or Manual File (Locally Rendered)
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


# 🔌 SQLAlchemy DB Session Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 💾 Save Local File (Server-Side)
@router.post("/admin/save-file")
def save_file(file_path: str = Form(...), content: str = Form(...)):
    try:
        path = Path(file_path)
        path.write_text(content, encoding='utf-8')
        return HTMLResponse("<h2>File saved successfully.</h2><a href='/admin'>Back to Admin</a>")
    except Exception as e:
        return HTMLResponse(f"<h2>Failed to save file: {e}</h2>", status_code=500)


# Save Manual File to GitHub
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

        # 🔍 Fetch current file SHA from GitHub
        get_response = requests.get(github_api_url, headers=headers)
        if get_response.status_code != 200:
            return HTMLResponse(f"<h2>GitHub Fetch Error: {get_response.status_code}<br>{get_response.text}</h2>", status_code=404)

        sha = get_response.json()["sha"]

        # 📝 Encode and prepare update payload
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        payload = {
            "message": f"Update manual file: {file_path}",
            "content": encoded_content,
            "sha": sha
        }

        put_response = requests.put(github_api_url, headers=headers, json=payload)

        if put_response.status_code in (200, 201):
            return HTMLResponse("<h2>Manual saved to GitHub successfully.</h2><a href='/auth/dashboard'>Return to Dashboard</a>")
        else:
            return HTMLResponse(f"<h2>GitHub Save Error: {put_response.status_code}<br>{put_response.text}</h2>", status_code=500)

    except Exception as e:
        return HTMLResponse(f"<h2>Exception while saving: {e}</h2>", status_code=500)



# This route renders the admin dashboard with a list of all PMCs pulled from your new database.
@router.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    db: Session = SessionLocal()
    pmc_list = db.query(PMC).all()
    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "pmcs": pmc_list  # Use 'pmcs' to match the template context
    })


# ➕ Show New PMC Form
@router.get("/admin/new-pmc", response_class=HTMLResponse)
def new_pmc_form(request: Request):
    return templates.TemplateResponse("pmc_form.html", {
        "request": request,
        "pms_integrations": ["Hostaway", "Guesty", "Lodgify", "Other"],
        "subscription_plans": ["Free", "Pro", "Enterprise"]
    })



# ➕ Add a New PMC Record
@router.post("/admin/add-pmc", response_class=RedirectResponse)
def add_pmc(
    request: Request,
    pmc_name: str = Form(...),
    contact_email: str = Form(...),
    main_contact: str = Form(...),
    subscription_plan: str = Form(...),
    pms_integration: str = Form(...),
    pms_client_id: str = Form(...),
    pms_secret: str = Form(...),
    active: bool = Form(False)
):
    db: Session = SessionLocal()
    new_pmc = PMC(
        pmc_name=pmc_name,
        email=contact_email,
        main_contact=main_contact,
        subscription_plan=subscription_plan,
        pms_integration=pms_integration,
        pms_client_id=pms_client_id,
        pms_secret=pms_secret,
        pms_account_id=get_next_account_id(db),
        active=active,
        sync_enabled=active
    )
    db.add(new_pmc)
    db.commit()
    return RedirectResponse(url="/admin/dashboard", status_code=HTTP_303_SEE_OTHER)


# 📖 Edit Manual File from GitHub
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
@router.post("/admin/sync-all")
def sync_all():
    try:
        sync_all_pmcs()
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    except Exception as e:
        print(f"[ERROR] Failed to sync all: {e}")
        return RedirectResponse(url="/admin/dashboard?status=error", status_code=303)


# ✅ Generate the next available PMS Account ID
def get_next_account_id(db: Session):
    last = db.query(PMC).order_by(PMC.pms_account_id.desc()).first()
    return (last.pms_account_id + 1) if last else 10000

    
# ✅ Update PMC Active Status (local DB only)
@router.post("/admin/update-status")
def update_status(payload: dict = Body(...)):
    record_id = payload.get("record_id")
    active = payload.get("active", False)

    if not record_id:
        return JSONResponse(status_code=400, content={"error": "Missing record_id"})

    db: Session = SessionLocal()
    try:
        pmc = db.query(PMC).filter_by(id=record_id).first()

        if not pmc:
            return JSONResponse(status_code=404, content={"error": "PMC not found"})

        pmc.active = active
        db.commit()
        return {"success": True}
    finally:
        db.close()



# 🔁 Trigger sync for one PMC by PMS Account ID
@router.post("/admin/sync-properties/{account_id}")
def sync_properties_for_pmc(account_id: str):
    try:
        sync_properties(account_id)
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    except Exception as e:
        print(f"[ERROR] Failed to sync: {e}")
        return RedirectResponse(url="/admin/dashboard?status=error", status_code=303)


# 💾 Save updated config content back to GitHub
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

        # 🔍 Retrieve current file SHA from GitHub
        get_response = requests.get(github_api_url, headers=headers)
        if get_response.status_code != 200:
            return HTMLResponse(
                f"<h2>GitHub Fetch Error: {get_response.status_code}<br>{get_response.text}</h2>",
                status_code=404
            )

        sha = get_response.json()["sha"]

        # 🧬 Encode new content and prepare commit
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        commit_message = f"Update config file: {file_path}"

        payload = {
            "message": commit_message,
            "content": encoded_content,
            "sha": sha
        }

        put_response = requests.put(github_api_url, headers=headers, json=payload)

        if put_response.status_code in (200, 201):
            return HTMLResponse(
                f"<h2>Config saved to GitHub successfully.</h2><a href='/auth/dashboard'>Return to Dashboard</a>"
            )
        else:
            return HTMLResponse(
                f"<h2>GitHub Save Error: {put_response.status_code}<br>{put_response.text}</h2>",
                status_code=500
            )

    except Exception as e:
        return HTMLResponse(f"<h2>Exception while saving: {e}</h2>", status_code=500)



# ⚙️ Load a GitHub-hosted config file into the web editor
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
            return HTMLResponse(
                f"<h2>GitHub Error: {response.status_code}<br>{response.text}</h2>",
                status_code=404
            )

        data = response.json()
        content = base64.b64decode(data['content']).decode('utf-8')

        return templates.TemplateResponse("editor.html", {
            "request": request,
            "file_path": file,
            "content": content
        })

    except Exception as e:
        return HTMLResponse(
            f"<h2>Error loading config file: {e}</h2>",
            status_code=500
        )


# 📝 Edit a GitHub-hosted file by loading its contents into the editor
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
            return HTMLResponse(
                f"<h2>GitHub Error: {response.status_code}<br>{response.text}</h2>",
                status_code=404
            )

        data = response.json()
        content = base64.b64decode(data['content']).decode('utf-8')

        return templates.TemplateResponse("editor.html", {
            "request": request,
            "file_path": file,
            "content": content
        })

    except Exception as e:
        return HTMLResponse(
            f"<h2>Error loading file: {e}</h2>",
            status_code=500
        )


# 🔧 Save a file to GitHub using the GitHub API
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

        # 📦 Prepare updated file payload
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



#💬 Chat UI Route Only (GET Request) This route only serves the HTML page for the chat UI
@admin_router.get("/chat-ui", response_class=HTMLResponse)
def chat_ui(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})


#💬 Chat Interface Route (Admin GPT Chat UI & Endpoint)
@router.api_route("/admin/chat", methods=["GET", "POST"])
async def chat_combined(request: Request):
    if request.method == "GET":
        return templates.TemplateResponse("chat.html", {"request": request})

    data = await request.json()
    user_message = data.get("message", "")

    if not user_message:
        return {"reply": "Please say something!"}

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are Sandy, a helpful and funny assistant."},
                {"role": "user", "content": user_message}
            ]
        )
        return {"reply": response.choices[0].message.content}
    except Exception as e:
        return {"reply": f"❌ ChatGPT Error: {e}"}


#This replaces the Airtable patch call and updates the active status in your SQL database using SQLAlchemy.
@router.post("/admin/update-status")
def update_pmc_status(payload: dict = Body(...)):
    from database import SessionLocal
    from models import PMC

    record_id = payload.get("record_id")
    active = payload.get("active", False)

    if not record_id:
        return JSONResponse(status_code=400, content={"error": "Missing record_id"})

    db = SessionLocal()
    try:
        pmc = db.query(PMC).filter(PMC.id == record_id).first()
        if not pmc:
            return JSONResponse(status_code=404, content={"error": "PMC not found"})

        pmc.active = active
        db.commit()
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        db.close()
