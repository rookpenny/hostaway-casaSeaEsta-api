import logging
import os
import requests
import json
import base64


from fastapi import APIRouter, Depends, Request, Form, Body, status, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError

from starlette.status import HTTP_303_SEE_OTHER
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

from database import SessionLocal, datetime, timedelta, date
from models import PMC, Property, ChatSession, ChatMessage
from utils.pms_sync import sync_properties, sync_all_pmcs
from openai import OpenAI

# ✅ Create the router object (do NOT create FastAPI app here)
router = APIRouter()

# ✅ Set up templates
templates = Jinja2Templates(directory="templates")

# ✅ Logging config
logging.basicConfig(level=logging.INFO)

# ✅ OpenAI client (optional)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

ADMIN_JOB_TOKEN = os.getenv("ADMIN_JOB_TOKEN", "")


# 🔌 SQLAlchemy DB Session Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()





# 💬 Recent Chats Overview
@router.get("/admin/chats", response_class=HTMLResponse)
def admin_chats(
    request: Request,
    db: Session = Depends(get_db),
    status: Optional[str] = Query(None),      # pre_booking | active | post_stay
    priority: Optional[str] = Query(None),    # urgent | unhappy
    q: Optional[str] = Query(None),           # search guest/property/snippet
):
    base_q = db.query(ChatSession)

    if status in {"pre_booking", "active", "post_stay"}:
        base_q = base_q.filter(ChatSession.reservation_status == status)

    sessions = (
        base_q.order_by(ChatSession.last_activity_at.desc())
        .limit(200)
        .all()
    )

    items = []
    q_lower = (q or "").strip().lower()

    for s in sessions:
        prop = s.property
        property_name = prop.property_name if prop else "Unknown property"

        last_msg = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == s.id)
            .order_by(ChatMessage.created_at.desc())
            .first()
        )

        has_urgent = db.query(ChatMessage).filter(
            ChatMessage.session_id == s.id,
            ChatMessage.sender == "guest",
            ChatMessage.category == "urgent",
        ).first() is not None

        has_negative = db.query(ChatMessage).filter(
            ChatMessage.session_id == s.id,
            ChatMessage.sender == "guest",
            ChatMessage.sentiment == "negative",
        ).first() is not None

        # Priority filter
        if priority == "urgent" and not has_urgent:
            continue
        if priority == "unhappy" and not has_negative:
            continue

        snippet = (last_msg.content[:120] + "…") if last_msg else ""
        guest_name = (getattr(s, "guest_name", None) or "").strip()
        status_val = getattr(s, "reservation_status", "pre_booking")
        needs_attention = (status_val == "active" and (has_urgent or has_negative))

        # Text search filter
        if q_lower:
            hay = f"{property_name} {guest_name} {snippet}".lower()
            if q_lower not in hay:
                continue

        items.append({
            "id": s.id,
            "property_name": property_name,
            "property_id": s.property_id,
            "guest_name": getattr(s, "guest_name", None),
            "arrival_date": getattr(s, "arrival_date", None),
            "departure_date": getattr(s, "departure_date", None),
            "reservation_status": getattr(s, "reservation_status", "pre_booking"),
            "last_activity_at": s.last_activity_at,
            "source": s.source,
            "is_verified": s.is_verified,  # legacy; OK to keep for now
            "last_snippet": snippet,
            "has_urgent": has_urgent,
            "has_negative": has_negative,
            "needs_attention": needs_attention,
        })

    return templates.TemplateResponse(
        "admin_chats.html",
        {
            "request": request,
            "sessions": items,
            "filters": {"status": status, "priority": priority, "q": q},
        }
    )



@router.get("/admin/analytics/chats")
def chats_analytics(db: Session = Depends(get_db)):
    by_status = dict(
        db.query(ChatSession.reservation_status, func.count(ChatSession.id))
          .group_by(ChatSession.reservation_status)
          .all()
    )

    urgent_sessions = db.query(ChatSession.id).join(ChatMessage).filter(
        ChatMessage.sender == "guest",
        ChatMessage.category == "urgent",
    ).distinct().count()

    unhappy_sessions = db.query(ChatSession.id).join(ChatMessage).filter(
        ChatMessage.sender == "guest",
        ChatMessage.sentiment == "negative",
    ).distinct().count()

    total_sessions = db.query(ChatSession.id).count()

    return {
        "total_sessions": total_sessions,
        "by_status": {
            "pre_booking": int(by_status.get("pre_booking", 0)),
            "active": int(by_status.get("active", 0)),
            "post_stay": int(by_status.get("post_stay", 0)),
        },
        "sessions_flagged": {
            "urgent": int(urgent_sessions),
            "unhappy": int(unhappy_sessions),
        }
    }


# 💬 Single Chat Conversation View
@router.get("/admin/chats/{session_id}", response_class=HTMLResponse)
def admin_chat_detail(session_id: int, request: Request, db: Session = Depends(get_db)):
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        return HTMLResponse("<h2>Chat session not found</h2>", status_code=404)

    prop = session.property

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    return templates.TemplateResponse(
        "admin_chat_detail.html",
        {
            "request": request,
            "session": session,
            "property": prop,
            "messages": messages,
        }
    )


# This route renders the admin dashboard with a list of all PMCs pulled from your new database.
@router.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    db: Session = SessionLocal()

    def serialize_pmc(pmc):
        return {
            "id": pmc.id,
            "pmc_name": pmc.pmc_name,
            "email": pmc.email,
            "main_contact": pmc.main_contact,
            "subscription_plan": pmc.subscription_plan,
            "pms_integration": pmc.pms_integration,
            "pms_api_key": pmc.pms_api_key,
            "pms_api_secret": pmc.pms_api_secret,
            "pms_account_id": pmc.pms_account_id,
            "active": pmc.active,
            "sync_enabled": pmc.sync_enabled,
            "last_synced_at": pmc.last_synced_at.isoformat() if pmc.last_synced_at else None
        }

    pmc_list = db.query(PMC).all()
    pmc_data = [serialize_pmc(p) for p in pmc_list]

    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "pmc": pmc_data  # ✅ Now it's safe to use `tojson` in the template
    })


from datetime import timedelta
from sqlalchemy import and_

ADMIN_JOB_TOKEN = os.getenv("ADMIN_JOB_TOKEN", "")

@router.post("/admin/jobs/refresh-session-status")
def refresh_session_status(request: Request, db: Session = Depends(get_db)):
    # Simple token gate (set ADMIN_JOB_TOKEN in Render env)
    token = request.headers.get("x-admin-job-token", "")
    if not ADMIN_JOB_TOKEN or token != ADMIN_JOB_TOKEN:
        return JSONResponse(status_code=401, content={"ok": False, "error": "Unauthorized"})

    cutoff = datetime.utcnow() - timedelta(days=90)

    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.last_activity_at >= cutoff)
        .all()
    )

    def to_date(x):
        if not x:
            return None
        if isinstance(x, date) and not isinstance(x, datetime):
            return x
        if isinstance(x, datetime):
            return x.date()
        if isinstance(x, str):
            try:
                return datetime.fromisoformat(x[:10]).date()
            except Exception:
                return None
        return None

    today = date.today()
    updated = 0

    for s in sessions:
        a = to_date(getattr(s, "arrival_date", None))
        d = to_date(getattr(s, "departure_date", None))

        if not a or not d:
            new_status = "pre_booking"
        elif a <= today <= d:
            new_status = "active"
        elif today > d:
            new_status = "post_stay"
        else:
            new_status = "pre_booking"

        if getattr(s, "reservation_status", "pre_booking") != new_status:
            s.reservation_status = new_status
            updated += 1

    db.commit()
    return {"ok": True, "checked": len(sessions), "updated": updated}

# Save Manual File to GitHub
@router.post("/admin/save-manual")
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




# ✅ Add this new route here:
@router.get("/admin/pmc-properties/{pmc_id}")
def pmc_properties(request: Request, pmc_id: int, db: Session = Depends(get_db)):
    properties = db.query(Property).filter(Property.pmc_id == pmc_id).all()
    return templates.TemplateResponse("pmc_properties.html", {
        "request": request,
        "properties": properties,
        "pmc_id": pmc_id
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
    pms_api_key: str = Form(...),
    pms_api_secret: str = Form(...),
    active: bool = Form(False)
):
    db: Session = SessionLocal()
    new_pmc = PMC(
        pmc_name=pmc_name,
        email=contact_email,
        main_contact=main_contact,
        subscription_plan=subscription_plan,
        pms_integration=pms_integration,
        pms_api_key=pms_api_key,
        pms_api_secret=pms_api_secret,
        pms_account_id=get_next_account_id(db),
        active=active,
        sync_enabled=active
    )
    db.add(new_pmc)
    db.commit()
    return RedirectResponse(url="/admin/dashboard", status_code=HTTP_303_SEE_OTHER)


# 📖 Edit Manual File from GitHub
@router.get("/edit-manual", response_class=HTMLResponse)
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


# 🔁 Trigger sync for one PMC by PMS Account ID

@router.post("/admin/sync-properties/{account_id}")
def sync_properties_for_pmc(account_id: str):
    from database import SessionLocal
    from models import PMC
    from utils.pms_sync import sync_properties

    db = SessionLocal()
    try:
        count = sync_properties(account_id)

        pmc = db.query(PMC).filter(PMC.pms_account_id == str(account_id)).first()
        synced_at = pmc.last_synced_at.isoformat() if pmc and pmc.last_synced_at else None

        return JSONResponse({
            "success": True,
            "message": f"Synced {count} properties",
            "synced_at": synced_at
        })
    except Exception as e:
        print(f"[ERROR] Failed to sync: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)
    finally:
        db.close()


# 💾 Save updated config content back to GitHub
@router.post("/admin/save-config")
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
@router.get("/edit-config", response_class=HTMLResponse)
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
@router.get("/edit-file", response_class=HTMLResponse)
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
@router.post("/admin/save-github-file")
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
@router.get("/chat-ui", response_class=HTMLResponse)
def chat_ui(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})


#💬 Chat Interface Route (Admin GPT Chat UI & Endpoint)
@router.api_route("/admin/chat", methods=["GET", "POST"])
async def chat_combined(request: Request):
    if request.method == "GET":
        return templates.TemplateResponse("chat.html", {"request": request})

    data = await request.json()
    user_message = data.get("message", "").strip()

    if not user_message:
        return {"reply": "Please say something!"}

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            temperature=0.85,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Sandy, a beachy, upbeat AI concierge for a vacation rental called Casa Sea Esta.\n\n"
                        "Always reply in the **same language** the guest uses.\n"
                        "Use **markdown formatting** to structure responses with:\n"
                        "- **Bold headers**\n"
                        "- *Italics where helpful*\n"
                        "- Bullet points\n"
                        "- Line breaks between sections\n"
                        "- Emojis to keep things friendly 🌞\n"
                        "- Google Maps links if places are mentioned\n\n"
                        "Keep replies warm, fun, and helpful — never robotic."
                    )
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ]
        )
        reply = response.choices[0].message.content
        return {"reply": reply}

    except Exception as e:
        return {"reply": f"❌ ChatGPT Error: {str(e)}"}


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

class PMCUpdateRequest(BaseModel):
    id: int
    pmc_name: str
    email: str | None
    main_contact: str | None
    subscription_plan: str | None
    pms_integration: str | None
    pms_api_key: str
    pms_api_secret: str
    pms_account_id: Optional[str]  # ✅ <-- ADD THIS LINE
    active: bool
    

from fastapi import HTTPException

@router.post("/admin/update-pmc")
def update_pmc(payload: PMCUpdateRequest):
    logging.warning("Received payload: %s", payload)
    db: Session = SessionLocal()
    try:
        print("🟡 Incoming payload:", payload)

        if payload.id:
            pmc = db.query(PMC).filter(PMC.id == payload.id).first()
            if not pmc:
                return JSONResponse(status_code=404, content={"error": "PMC not found"})
        else:
            pmc = PMC()
            # ✅ assign account ID for new PMC
            last = db.query(PMC).order_by(PMC.pms_account_id.desc()).first()
            pmc.pms_account_id = (int(last.pms_account_id) + 1) if last and last.pms_account_id else 10000
            pmc.sync_enabled = True

        pmc.pmc_name = payload.pmc_name
        pmc.email = payload.email
        pmc.main_contact = payload.main_contact
        pmc.subscription_plan = payload.subscription_plan
        pmc.pms_integration = payload.pms_integration
        pmc.pms_api_key = payload.pms_api_key
        pmc.pms_api_secret = payload.pms_api_secret
        pmc.pms_account_id = payload.pms_account_id  # ✅ add this to support form input
        pmc.active = payload.active

        db.add(pmc)
        db.commit()
        return {"success": True}
    except RequestValidationError as ve:
        return JSONResponse(status_code=422, content={"error": ve.errors()})
    except Exception as e:
        db.rollback()
        logging.exception("🔥 Exception during PMC update")
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        db.close()


# 🗑️ Delete PMC
@router.delete("/admin/delete-pmc/{pmc_id}")
def delete_pmc(pmc_id: int):
    db = SessionLocal()
    try:
        pmc = db.query(PMC).filter(PMC.id == pmc_id).first()
        if not pmc:
            return JSONResponse(status_code=404, content={"error": "PMC not found"})
        db.delete(pmc)
        db.commit()
        return {"success": True}
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        db.close()

@router.post("/admin/update-properties")
def update_properties(payload: list[dict], db: Session = Depends(get_db)):
    try:
        for item in payload:
            prop = db.query(Property).filter(Property.id == item["id"]).first()
            if prop:
                prop.sandy_enabled = item["sandy_enabled"]
        db.commit()
        return {"success": True}
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/admin/pmc-properties-json/{pmc_id}")
def get_pmc_properties_json(pmc_id: int, db: Session = Depends(get_db)):
    properties = db.query(Property).filter(Property.pmc_id == pmc_id).all()
    return {
        "properties": [
            {
                "id": p.id,
                "property_name": p.property_name,
                "pms_property_id": p.pms_property_id,
                "sandy_enabled": p.sandy_enabled,
            }
            for p in properties
        ]
    }

