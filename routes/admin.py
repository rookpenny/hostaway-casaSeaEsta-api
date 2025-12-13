import logging
import os
import requests
import json
import base64
import re

from fastapi import APIRouter, Depends, Request, Form, Body, status, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError

from starlette.status import HTTP_303_SEE_OTHER
from sqlalchemy.orm import Session
import sqlalchemy as sa
from sqlalchemy import func, and_
from pydantic import BaseModel
from typing import Optional, Dict, List
from pathlib import Path

from database import SessionLocal
from datetime import datetime, timedelta, date

from models import PMC, Property, ChatSession, ChatMessage, PMCUser
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
SUMMARY_MODEL = os.getenv("OPENAI_SUMMARY_MODEL", "gpt-4o-mini")
ADMIN_IDENTITY_SESSION_KEY = os.getenv("ADMIN_IDENTITY_SESSION_KEY", "admin_email")

# Escalation thresholds (env override friendly)
ESCALATE_LOW_HEAT = int(os.getenv("ESCALATE_LOW_HEAT", "35"))
ESCALATE_MEDIUM_HEAT = int(os.getenv("ESCALATE_MEDIUM_HEAT", "60"))
ESCALATE_HIGH_HEAT = int(os.getenv("ESCALATE_HIGH_HEAT", "85"))


# 🔌 SQLAlchemy DB Session Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def require_super(request: Request, db: Session = Depends(get_db)):
    role, _, _ = get_user_role_and_scope(request, db)
   
    
    if role != "super":
        raise HTTPException(status_code=403, detail="Forbidden")



def enforce_assignee_in_pmc(request: Request, db: Session, assigned_to: str):
    assigned_to = (assigned_to or "").strip().lower()
    if not assigned_to:
        return

    user_role, pmc_obj, pmc_user = get_user_role_and_scope(request, db)

    if user_role == "super":
        return

    if user_role == "pmc" and not pmc_obj:
        raise HTTPException(status_code=403, detail="PMC account not linked")

    ok = (
        db.query(PMCUser)
          .filter(
              PMCUser.pmc_id == pmc_obj.id,
              sa.func.lower(PMCUser.email) == assigned_to,
              PMCUser.is_active == True
          )
          .first()
    )
    if not ok:
        raise HTTPException(status_code=403, detail="Assignee not in your PMC team")



def get_current_admin_identity(request: Request) -> Optional[str]:
    """
    Future-proof identity lookup.
    Order of precedence:
      1) AuthenticationMiddleware (request.scope["user"]) if installed
      2) Session key ADMIN_IDENTITY_SESSION_KEY (explicit admin identity)
      3) Session user dict (current Google login flow)
      4) Optional headers (internal tooling / proxy auth)
    NEVER throws.
    """

    # 1) Auth middleware (safe access via scope)
    try:
        user = request.scope.get("user")
        if user and getattr(user, "is_authenticated", False):
            for attr in ("email", "username", "name"):
                val = getattr(user, attr, None)
                if val and str(val).strip():
                    return str(val).strip().lower()
    except Exception:
        pass

    # 2) Explicit session key (best practice going forward)
    try:
        sess_val = request.session.get(ADMIN_IDENTITY_SESSION_KEY)
        if sess_val and str(sess_val).strip():
            return str(sess_val).strip().lower()
    except Exception:
        pass

    # 3) CURRENT Google login flow: request.session["user"]["email"]
    try:
        sess_user = request.session.get("user")
        if isinstance(sess_user, dict):
            email = (sess_user.get("email") or "").strip()
            if email:
                return email.lower()
    except Exception:
        pass

    # 4) Optional header fallback
    try:
        hdr = request.headers.get("x-admin-email") or request.headers.get("x-admin-user")
        if hdr and hdr.strip():
            return hdr.strip().lower()
    except Exception:
        pass

    return None


def is_super_admin(email: Optional[str]) -> bool:
    """
    Minimal role check.
    - If ADMIN_EMAILS env is set (comma-separated), those are super.
    - Otherwise: fallback to allowlist of your own email (edit as needed).
    """
    if not email:
        return False

    allow = os.getenv("ADMIN_EMAILS", "")
    if allow.strip():
        allowed = {e.strip().lower() for e in allow.split(",") if e.strip()}
        return email.lower() in allowed

    # TODO: set ADMIN_EMAILS in Render and delete this fallback
    return email.lower() in {
        "corbett.jarrod@gmail.com",   # <-- change
    }

def get_user_role_and_scope(request: Request, db: Session):
    """
    Returns: (user_role, pmc_obj_or_none, pmc_user_or_none)
      - user_role: "super" | "pmc"
      - pmc_obj: PMC row for scope (if pmc)
      - pmc_user: PMCUser row (optional, useful for name/role)
    """
    email = get_current_admin_identity(request)

    if is_super_admin(email):
        return "super", None, None

    if not email:
        return "pmc", None, None

    email_l = email.strip().lower()

    # ✅ 1) PMC staff table lookup (preferred)
    pmc_user = (
        db.query(PMCUser)
          .filter(func.lower(PMCUser.email) == email_l, PMCUser.is_active == True)
          .first()
    )
    if pmc_user:
        pmc = db.query(PMC).filter(PMC.id == pmc_user.pmc_id).first()
        return "pmc", pmc, pmc_user

    # ✅ 2) Optional fallback: PMC “owner” email on PMC table
    pmc = db.query(PMC).filter(func.lower(PMC.email) == email_l).first()
    if pmc:
        return "pmc", pmc, None

    return "pmc", None, None


def require_pmc_linked(user_role: str, pmc_obj: Optional[PMC]):
    if user_role == "pmc" and not pmc_obj:
        raise HTTPException(status_code=403, detail="PMC account not linked")


def require_session_in_scope(request: Request, db: Session, session_id: int) -> ChatSession:
    """
    Super: can access any session
    PMC: can access only sessions whose property belongs to their PMC
    """
    user_role, pmc_obj, pmc_user = get_user_role_and_scope(request, db)
    require_pmc_linked(user_role, pmc_obj)

    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Not found")

    if user_role == "pmc":
        prop = db.query(Property).filter(Property.id == session.property_id).first()
        if not prop or prop.pmc_id != pmc_obj.id:
            raise HTTPException(status_code=403, detail="Forbidden")

    return session


def require_property_in_scope(request: Request, db: Session, property_id: int) -> Property:
    """
    Super: can access any property
    PMC: can access only properties that belong to their PMC
    """
    user_role, pmc_obj, pmc_user = get_user_role_and_scope(request, db)
    require_pmc_linked(user_role, pmc_obj)

    prop = db.query(Property).filter(Property.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    if user_role == "pmc" and prop.pmc_id != pmc_obj.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    return prop


def require_file_in_scope(request: Request, db: Session, file_path: str) -> str:
    user_role, pmc_obj, _ = get_user_role_and_scope(request, db)
    require_pmc_linked(user_role, pmc_obj)

    file_path = (file_path or "").strip().lstrip("/").strip()

    if user_role == "super":
        return file_path

    props = (
        db.query(Property)
          .filter(Property.pmc_id == pmc_obj.id)
          .all()
    )

    for p in props:
        base = (getattr(p, "data_folder_path", None) or "").strip().strip("/")
        if base and (file_path == base or file_path.startswith(base + "/")):
            return file_path

    raise HTTPException(status_code=403, detail="Forbidden file")



def decay_heat(heat_value: int, last_activity_at: Optional[datetime]) -> int:
    """
    Heat decay so old fires drop.
    Simple rule: -10 per day since last activity (max -50).
    """
    if not last_activity_at:
        return heat_value

    try:
        now = datetime.utcnow()

        # If last_activity_at is timezone-aware, make "now" aware too
        if getattr(last_activity_at, "tzinfo", None) is not None:
            from datetime import timezone
            now = datetime.now(timezone.utc)

        delta = now - last_activity_at
        days = max(0, int(delta.total_seconds() // 86400))
        penalty = min(50, days * 10)
        return max(0, int(heat_value) - penalty)
    except Exception:
        return heat_value



def extract_next_action(ai_summary: Optional[str]) -> Optional[str]:
    """
    Pull a short 'next action' string from the AI summary markdown.

    Looks for the section header "Recommended next action" (markdown bold),
    then returns:
      1) first bullet/numbered item under it, else
      2) first non-empty line under it
    Stops when the next bold-section header starts.
    """
    if not ai_summary:
        return None

    text = ai_summary.strip()

    # Find start of the section
    m = re.search(r"\*\*Recommended next action\*\*\s*", text, flags=re.IGNORECASE)
    if not m:
        return None

    tail = text[m.end():].lstrip()

    # Stop at the next bold section header (**Something**)
    stop = re.search(r"\n\s*\*\*[^*]+\*\*\s*", tail)
    if stop:
        tail = tail[:stop.start()].strip()

    if not tail:
        return None

    # Prefer first bullet or numbered item
    item = re.search(r"^\s*(?:[-*]|[0-9]+\.)\s+(.+)$", tail, flags=re.MULTILINE)
    if item:
        return item.group(1).strip()[:140]

    # Otherwise first non-empty line
    for line in tail.splitlines():
        line = line.strip().lstrip("-*").strip()
        if line:
            return line[:140]

    return None



def desired_escalation_level(heat: int) -> Optional[str]:
    if heat >= ESCALATE_HIGH_HEAT:
        return "high"
    if heat >= ESCALATE_MEDIUM_HEAT:
        return "medium"
    return None


def escalation_rank(level: Optional[str]) -> int:
    order = {None: 0, "": 0, "low": 1, "medium": 2, "high": 3}
    return order.get((level or "").lower(), 0)




# 💬 Recent Chats Overview
# 💬 Recent Chats Overview
@router.get("/admin/chats", response_class=HTMLResponse)
def admin_chats(
    request: Request,
    db: Session = Depends(get_db),
    status: Optional[str] = Query(None),          # pre_booking | active | post_stay
    priority: Optional[str] = Query(None),        # urgent | unhappy (filter by flags)
    q: Optional[str] = Query(None),               # search guest/property/snippet
    pmc_id: Optional[str] = Query(None),          # string to avoid "" int parsing (super-only filter)
    property_id: Optional[str] = Query(None),     # string to avoid "" int parsing
    mine: Optional[int] = Query(None),            # 1 = only my assigned chats
    assigned_to: Optional[str] = Query(None),     # exact match
):
    # ---- role & scope ----
    user_role, pmc_obj, pmc_user = get_user_role_and_scope(request, db)

    # If PMC user but not mapped to a PMC record, block
    if user_role == "pmc" and not pmc_obj:
        raise HTTPException(status_code=403, detail="PMC account not linked")

    # ---- helpers ----
    def to_int_or_none(v: Optional[str]) -> Optional[int]:
        v = (v or "").strip()
        return int(v) if v.isdigit() else None

    def activity_bucket(cnt24: int, cnt7: int) -> str:
        if cnt24 >= 5:
            return "Spiking"
        if cnt24 >= 2:
            return "Active"
        if cnt7 > 0:
            return "Cooling"
        return "Quiet"

    def derive_signals(
        has_urgent: bool,
        has_negative: bool,
        cnt24: int,
        cnt7: int,
        status_val: str,
    ) -> list[str]:
        """
        Signals = emotion-style tags derived from existing data (no new models).
        Returned as lowercase tokens to match your Jinja checks.
        Caps to 2 for readability.
        """
        signals: list[str] = []

        if has_urgent:
            signals.append("panicked")
        if has_negative:
            signals.append("upset")

        if has_negative and cnt24 >= 3:
            signals.append("angry")

        # chatter without urgent/negative
        if (not has_urgent) and (not has_negative) and (cnt7 >= 3 or cnt24 >= 2):
            signals.append("confused")

        if status_val == "active" and (has_urgent or has_negative):
            signals.append("stressed")

        if not signals:
            signals.append("calm")

        # Dedup + cap
        out: list[str] = []
        seen = set()
        for s in signals:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out[:2]

    def heat_score(has_urgent: bool, has_negative: bool, cnt24: int, cnt7: int) -> int:
        score = 0
        score += 50 if has_urgent else 0
        score += 25 if has_negative else 0
        score += min(25, cnt24 * 5)
        score += min(10, cnt7)
        return min(100, score)

    def priority_bucket(heat: int) -> str:
        """
        Topic/ops-based priority levels (not emotional).
        """
        if heat >= 85:
            return "critical"
        if heat >= 60:
            return "attention"
        return "routine"

    pmc_id_int = to_int_or_none(pmc_id)
    property_id_int = to_int_or_none(property_id)

    # ---- dropdown data (scoped) ----
    if user_role == "super":
        pmcs = db.query(PMC).order_by(PMC.pmc_name.asc()).all()
        properties = db.query(Property).order_by(Property.property_name.asc()).all()
    else:
        pmcs = [pmc_obj]
        properties = (
            db.query(Property)
              .filter(Property.pmc_id == pmc_obj.id)
              .order_by(Property.property_name.asc())
              .all()
        )

    allowed_property_ids = {p.id for p in properties} if user_role == "pmc" else None

    # ---- base query (scoped) ----
    base_q = db.query(ChatSession)

    # PMC scope: force it for PMC users (ignore pmc_id param entirely)
    if user_role == "pmc":
        base_q = (
            base_q.join(Property, ChatSession.property_id == Property.id)
                  .filter(Property.pmc_id == pmc_obj.id)
        )
    else:
        # super can filter by pmc_id
        if pmc_id_int is not None:
            base_q = (
                base_q.join(Property, ChatSession.property_id == Property.id)
                      .filter(Property.pmc_id == pmc_id_int)
            )

    # Property filter (PMC cannot access other properties)
    if property_id_int is not None:
        if user_role == "pmc" and property_id_int not in allowed_property_ids:
            raise HTTPException(status_code=403, detail="Forbidden property")
        base_q = base_q.filter(ChatSession.property_id == property_id_int)

    # Reservation status filter
    if status in {"pre_booking", "active", "post_stay"}:
        base_q = base_q.filter(ChatSession.reservation_status == status)

    # ----- assignee filters -----
    effective_assignee: Optional[str] = (assigned_to or "").strip() or None
    if mine:
        me = get_current_admin_identity(request)
        if me:
            effective_assignee = me

    if effective_assignee:
        base_q = base_q.filter(ChatSession.assigned_to == effective_assignee)

    sessions = (
        base_q.order_by(ChatSession.last_activity_at.desc())
              .limit(200)
              .all()
    )

    session_ids = [int(s.id) for s in sessions]
    now = datetime.utcnow()
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)

    counts_24h: Dict[int, int] = {}
    counts_7d: Dict[int, int] = {}
    last_msg_map: Dict[int, ChatMessage] = {}
    urgent_ids: set[int] = set()
    negative_ids: set[int] = set()

    if session_ids:
        # Last message per session (Postgres DISTINCT ON)
        latest_msgs = (
            db.query(ChatMessage)
              .filter(ChatMessage.session_id.in_(session_ids))
              .order_by(ChatMessage.session_id.asc(), ChatMessage.created_at.desc())
              .distinct(ChatMessage.session_id)
              .all()
        )
        last_msg_map = {int(m.session_id): m for m in latest_msgs}

        urgent_ids = set(
            int(sid) for (sid,) in (
                db.query(ChatMessage.session_id)
                  .filter(
                      ChatMessage.session_id.in_(session_ids),
                      ChatMessage.sender == "guest",
                      ChatMessage.category == "urgent",
                  )
                  .distinct()
                  .all()
            )
        )

        negative_ids = set(
            int(sid) for (sid,) in (
                db.query(ChatMessage.session_id)
                  .filter(
                      ChatMessage.session_id.in_(session_ids),
                      ChatMessage.sender == "guest",
                      ChatMessage.sentiment == "negative",
                  )
                  .distinct()
                  .all()
            )
        )

        for sid, cnt in (
            db.query(ChatMessage.session_id, func.count(ChatMessage.id))
              .filter(ChatMessage.session_id.in_(session_ids), ChatMessage.created_at >= since_24h)
              .group_by(ChatMessage.session_id)
              .all()
        ):
            counts_24h[int(sid)] = int(cnt)

        for sid, cnt in (
            db.query(ChatMessage.session_id, func.count(ChatMessage.id))
              .filter(ChatMessage.session_id.in_(session_ids), ChatMessage.created_at >= since_7d)
              .group_by(ChatMessage.session_id)
              .all()
        ):
            counts_7d[int(sid)] = int(cnt)

    items = []
    q_lower = (q or "").strip().lower()
    auto_escalation_updates = 0

    for s in sessions:
        sid = int(s.id)

        prop = s.property
        property_name = prop.property_name if prop else "Unknown property"
        guest_name = (getattr(s, "guest_name", None) or "").strip()

        last_msg = last_msg_map.get(sid)
        snippet = ""
        if last_msg and getattr(last_msg, "content", None):
            snippet = last_msg.content.strip()
            snippet = (snippet[:120] + "…") if len(snippet) > 120 else snippet

        has_urgent = sid in urgent_ids
        has_negative = sid in negative_ids

        # Filter dropdown "priority" is still based on flags
        if priority == "urgent" and not has_urgent:
            continue
        if priority == "unhappy" and not has_negative:
            continue

        if q_lower:
            hay = f"{property_name} {guest_name} {snippet}".lower()
            if q_lower not in hay:
                continue

        status_val = getattr(s, "reservation_status", "pre_booking")
        needs_attention = (status_val == "active" and (has_urgent or has_negative))

        cnt24 = counts_24h.get(sid, 0)
        cnt7 = counts_7d.get(sid, 0)

        # Score (raw -> multiplier -> decay)
        raw_heat = heat_score(has_urgent, has_negative, cnt24, cnt7)

        multiplier = 1.0
        if has_urgent:
            multiplier += 0.30
        if has_negative:
            multiplier += 0.15
        if status_val == "active":
            multiplier += 0.10

        heat_boosted = int(min(100, round(raw_heat * multiplier)))
        heat = decay_heat(heat_boosted, getattr(s, "last_activity_at", None))

        # Ops priority bucket (topic-based labels)
        priority_level = priority_bucket(heat)

        next_action = extract_next_action(getattr(s, "ai_summary", None))
        activity_label = activity_bucket(cnt24, cnt7)

        signals = derive_signals(
            has_urgent=has_urgent,
            has_negative=has_negative,
            cnt24=cnt24,
            cnt7=cnt7,
            status_val=status_val,
        )

        # Auto-escalation (only escalate up; never downgrade)
        is_resolved = bool(getattr(s, "is_resolved", False))
        current_level = (getattr(s, "escalation_level", None) or "").lower() or None
        desired_level = desired_escalation_level(heat)

        if (not is_resolved) and escalation_rank(desired_level) > escalation_rank(current_level):
            s.escalation_level = desired_level
            s.updated_at = datetime.utcnow()
            db.add(s)
            auto_escalation_updates += 1

        items.append({
            "id": s.id,
            "property_name": property_name,
            "property_id": s.property_id,
            "guest_name": getattr(s, "guest_name", None),
            "reservation_status": status_val,
            "last_activity_at": s.last_activity_at,
            "source": getattr(s, "source", None),
            "last_snippet": snippet,

            # Signals
            "signals": signals,
            "has_urgent": has_urgent,
            "has_negative": has_negative,
            "needs_attention": needs_attention,

            # Activity
            "msg_24h": cnt24,
            "msg_7d": cnt7,
            "activity_label": activity_label,

            # Priority score + label
            "heat_raw": raw_heat,
            "heat_boosted": heat_boosted,
            "heat": heat,
            "priority_level": priority_level,

            # AI assist
            "next_action": next_action,

            # Ops state
            "assigned_to": getattr(s, "assigned_to", None),
            "escalation_level": getattr(s, "escalation_level", None),
            "is_resolved": is_resolved,
        })

    if auto_escalation_updates:
        db.commit()

    # Sort: priority score first, then recency
    items.sort(
        key=lambda x: (x["heat"], x["last_activity_at"] or datetime.min),
        reverse=True
    )

    # ---- analytics (scoped) ----
    analytics_q = db.query(ChatSession)
    if user_role == "pmc":
        analytics_q = (
            analytics_q.join(Property, ChatSession.property_id == Property.id)
                       .filter(Property.pmc_id == pmc_obj.id)
        )

    by_status = dict(
        analytics_q.with_entities(ChatSession.reservation_status, func.count(ChatSession.id))
                   .group_by(ChatSession.reservation_status)
                   .all()
    )

    urgent_q = db.query(ChatSession.id).join(ChatMessage)
    unhappy_q = db.query(ChatSession.id).join(ChatMessage)

    if user_role == "pmc":
        urgent_q = (
            urgent_q.join(Property, ChatSession.property_id == Property.id)
                    .filter(Property.pmc_id == pmc_obj.id)
        )
        unhappy_q = (
            unhappy_q.join(Property, ChatSession.property_id == Property.id)
                     .filter(Property.pmc_id == pmc_obj.id)
        )

    urgent_sessions = (
        urgent_q.filter(ChatMessage.sender == "guest", ChatMessage.category == "urgent")
                .distinct()
                .count()
    )
    unhappy_sessions = (
        unhappy_q.filter(ChatMessage.sender == "guest", ChatMessage.sentiment == "negative")
                 .distinct()
                 .count()
    )

    analytics = {
        "pre_booking": int(by_status.get("pre_booking", 0)),
        "active": int(by_status.get("active", 0)),
        "post_stay": int(by_status.get("post_stay", 0)),
        "urgent_sessions": int(urgent_sessions),
        "unhappy_sessions": int(unhappy_sessions),
    }

    return templates.TemplateResponse(
        "admin_chats.html",
        {
            "request": request,
            "sessions": items,
            "filters": {
                "status": status,
                "priority": priority,
                "q": q,
                # PMC users should not use pmc_id filter (keep stable)
                "pmc_id": (str(pmc_obj.id) if user_role == "pmc" else (pmc_id or "")),
                "property_id": property_id or "",
                "mine": bool(mine),
                "assigned_to": effective_assignee or "",
            },
            "analytics": analytics,
            "pmcs": pmcs,
            "properties": properties,
            "user_role": user_role,  # ✅ lets template hide PMC dropdown for PMCs
        }
    )







def desired_escalation_level(heat: int) -> Optional[str]:
    if heat >= ESCALATE_HIGH_HEAT:
        return "high"
    if heat >= ESCALATE_MEDIUM_HEAT:
        return "medium"
    if heat >= ESCALATE_LOW_HEAT:
        return "low"
    return None

def escalation_rank(level: Optional[str]) -> int:
    order = {None: 0, "": 0, "low": 1, "medium": 2, "high": 3}
    return order.get((level or "").lower(), 0)

@router.post("/admin/chats/{session_id}/resolve")
def resolve_chat(session_id: int, request: Request, db: Session = Depends(get_db)):
    s = require_session_in_scope(request, db, session_id)

    s.is_resolved = True
    s.resolved_at = datetime.utcnow()
    s.updated_at = datetime.utcnow()
    db.add(s)
    db.commit()
    return {"ok": True, "is_resolved": True, "resolved_at": s.resolved_at.isoformat()}



@router.post("/admin/chats/{session_id}/unresolve")
def unresolve_chat(session_id: int, request: Request, db: Session = Depends(get_db)):
    s = require_session_in_scope(request, db, session_id)

    s.is_resolved = False
    s.resolved_at = None
    s.updated_at = datetime.utcnow()
    db.add(s)
    db.commit()
    return {"ok": True, "is_resolved": False}


@router.post("/admin/chats/{session_id}/escalate")
def escalate_chat(session_id: int, request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    s = require_session_in_scope(request, db, session_id)

    level = (payload.get("level") or "").strip().lower()
    if level not in {"low", "medium", "high", ""}:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid level"})

    if bool(getattr(s, "is_resolved", False)):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Session is resolved. Reopen to change escalation."})

    s.escalation_level = level or None
    s.updated_at = datetime.utcnow()
    db.add(s)
    db.commit()
    return {"ok": True, "escalation_level": s.escalation_level}



@router.post("/admin/chats/{session_id}/assign")
def assign_chat(session_id: int, request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    s = require_session_in_scope(request, db, session_id)

    assigned_to = (payload.get("assigned_to") or "").strip()

    # Optional: enforce "their own staff" — see note below
    enforce_assignee_in_pmc(request, db, assigned_to)

    s.assigned_to = assigned_to or None
    s.updated_at = datetime.utcnow()
    db.add(s)
    db.commit()
    return {"ok": True, "assigned_to": s.assigned_to}



@router.post("/admin/chats/{session_id}/note")
def set_internal_note(session_id: int, request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    s = require_session_in_scope(request, db, session_id)

    note = (payload.get("note") or "").strip()
    s.internal_note = note or None
    s.updated_at = datetime.utcnow()
    db.add(s)
    db.commit()
    return {"ok": True}



@router.post("/admin/chats/{session_id}/summarize")
async def summarize_chat(session_id: int, request: Request, db: Session = Depends(get_db)):
    session = require_session_in_scope(request, db, session_id)

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(40)
        .all()
    )
    messages = list(reversed(messages))
    convo = "\n".join([f"{m.sender.upper()}: {m.content}" for m in messages])

    system = (
        "You are an operations assistant for a short-term rental host. "
        "Summarize the conversation for an admin dashboard. "
        "Output markdown with these sections:\n"
        "1) **What the guest wants**\n"
        "2) **Key facts** (dates, unit details, constraints)\n"
        "3) **Risks / sentiment** (urgent/unhappy signals)\n"
        "4) **Recommended next action** (clear steps)\n"
        "Keep it short and scannable."
    )

    try:
        resp = client.chat.completions.create(
            model=SUMMARY_MODEL,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": convo},
            ],
        )
        summary = resp.choices[0].message.content.strip()
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

    session.ai_summary = summary
    session.ai_summary_updated_at = datetime.utcnow()
    db.add(session)
    db.commit()

    return {"ok": True, "summary": summary, "updated_at": session.ai_summary_updated_at.isoformat()}



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
    session = require_session_in_scope(request, db, session_id)

    prop = session.property
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    user_role, _, _ = get_user_role_and_scope(request, db)

    return templates.TemplateResponse(
        "admin_chat_detail.html",
        {
            "request": request,
            "session": session,
            "property": prop,
            "messages": messages,
            "user_role": user_role,
        }
    )




# This route renders the admin dashboard with a list of all PMCs pulled from your new database.
@router.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    user_role, pmc_obj, pmc_user = get_user_role_and_scope(request, db)

    # If PMC user but not mapped to a PMC record, block
    if user_role == "pmc" and not pmc_obj:
        return HTMLResponse(
            "<h2>Access denied</h2><p>Your Google account isn’t linked to a PMC.</p>",
            status_code=403
        )

    # Properties scoped by role
    if user_role == "super":
        properties = db.query(Property).order_by(Property.property_name.asc()).all()
    else:
        properties = (
            db.query(Property)
              .filter(Property.pmc_id == pmc_obj.id)
              .order_by(Property.property_name.asc())
              .all()
        )

    # Optional: keep this for future super “PMCs” view
    pmc_data = []
    if user_role == "super":
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

        pmc_list = db.query(PMC).order_by(PMC.pmc_name.asc()).all()
        pmc_data = [serialize_pmc(p) for p in pmc_list]

    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "user_role": user_role,                         # ✅ used to hide/show nav + views
            "pmc_name": (pmc_obj.pmc_name if pmc_obj else "HostScout"),  # ✅ sidebar label
            "properties": properties,                       # ✅ drives PMC property cards
            "now": datetime.utcnow(),                       # ✅ last_synced relative text
            "pmc": pmc_data,                                # optional (super only)
        }
    )



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
def save_manual_file(
    request: Request,
    file_path: str = Form(...),
    content: str = Form(...),
    db: Session = Depends(get_db),
):
    # 🔒 Scope check (super = all, PMC = own property folders only)
    file_path = require_file_in_scope(request, db, file_path)

    try:
        repo_owner = "rookpenny"
        repo_name = "hostscout_data"
        github_token = os.getenv("GITHUB_TOKEN")
        github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}"

        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
        }

        # 🔍 Fetch current file SHA (file must exist for "manual" edits)
        get_response = requests.get(github_api_url, headers=headers)
        if get_response.status_code != 200:
            return HTMLResponse(
                f"<h2>GitHub Fetch Error: {get_response.status_code}<br>{get_response.text}</h2>",
                status_code=404,
            )

        sha = get_response.json().get("sha")
        if not sha:
            return HTMLResponse("<h2>GitHub Fetch Error: missing SHA</h2>", status_code=500)

        # 📝 Encode + commit
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        payload = {
            "message": f"Update manual file: {file_path}",
            "content": encoded_content,
            "sha": sha,
        }

        put_response = requests.put(github_api_url, headers=headers, json=payload)

        if put_response.status_code in (200, 201):
            return HTMLResponse(
                "<h2>Manual saved to GitHub successfully.</h2><a href='/auth/dashboard'>Return to Dashboard</a>"
            )

        return HTMLResponse(
            f"<h2>GitHub Save Error: {put_response.status_code}<br>{put_response.text}</h2>",
            status_code=500,
        )

    except Exception as e:
        return HTMLResponse(f"<h2>Exception while saving: {e}</h2>", status_code=500)



# ✅ Add this new route here:
@router.get("/admin/pmc-properties/{pmc_id}")
def pmc_properties(request: Request, pmc_id: int, db: Session = Depends(get_db)):
    user_role, pmc_obj, _ = get_user_role_and_scope(request, db)
    if user_role == "pmc":
        if not pmc_obj or pmc_obj.id != pmc_id:
            raise HTTPException(status_code=403, detail="Forbidden")
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
    active: bool = Form(False),
    db: Session = Depends(get_db),
):
    require_super(request, db)  # recommended

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
        sync_enabled=active,
    )
    db.add(new_pmc)
    db.commit()
    return RedirectResponse(url="/admin/dashboard", status_code=HTTP_303_SEE_OTHER)


# 📖 Edit Manual File from GitHub
@router.get("/edit-config", response_class=HTMLResponse)
def edit_config_file(
    request: Request,
    file: str,
    db: Session = Depends(get_db),
):
    # 🔒 Scope check (super = all, PMC = own property folders only)
    file = require_file_in_scope(request, db, file)

    repo_owner = "rookpenny"
    repo_name = "hostscout_data"
    github_token = os.getenv("GITHUB_TOKEN")
    github_api_url = (
        f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file}"
    )

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
    }

    response = requests.get(github_api_url, headers=headers)

    if response.status_code != 200:
        return HTMLResponse(
            f"<h2>GitHub Error: {response.status_code}<br>{response.text}</h2>",
            status_code=404,
        )

    data = response.json()

    try:
        content = base64.b64decode(data["content"]).decode("utf-8")
    except Exception:
        return HTMLResponse(
            "<h2>Error decoding file content</h2>",
            status_code=500,
        )

    return templates.TemplateResponse(
        "editor.html",
        {
            "request": request,
            "file_path": file,
            "content": content,
        },
    )




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
def get_next_account_id(db: Session) -> str:
    last = db.query(PMC).order_by(PMC.pms_account_id.desc()).first()
    if not last or not (last.pms_account_id or "").isdigit():
        return "10000"
    return str(int(last.pms_account_id) + 1)



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
def save_config_file(
    request: Request,
    file_path: str = Form(...),
    content: str = Form(...),
    db: Session = Depends(get_db),
):
  file_path = require_file_in_scope(request, db, file_path)

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
def edit_config_file(request: Request, file: str, db: Session = Depends(get_db)):
    
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
def edit_file_from_github(request: Request, file: str, db: Session = Depends(get_db)):
    file = require_file_in_scope(request, db, file)

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
    content = base64.b64decode(data["content"]).decode("utf-8")

    return templates.TemplateResponse("editor.html", {
        "request": request,
        "file_path": file,
        "content": content
    })


# 🔧 Save a file to GitHub using the GitHub API
@router.post("/admin/save-github-file")
def save_github_file(
    request: Request,
    file_path: str = Form(...),
    content: str = Form(...),
    db: Session = Depends(get_db),
):
    file_path = require_file_in_scope(request, db, file_path)

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
    
@router.post("/admin/update-pmc")
def update_pmc(request: Request, payload: PMCUpdateRequest, db: Session = Depends(get_db)):
    require_super(request, db)

    logging.warning("Received payload: %s", payload)

    try:
        if payload.id:
            pmc = db.query(PMC).filter(PMC.id == payload.id).first()
            if not pmc:
                return JSONResponse(status_code=404, content={"error": "PMC not found"})
        else:
            pmc = PMC()
            pmc.pms_account_id = get_next_account_id(db)
            pmc.sync_enabled = True

        pmc.pmc_name = payload.pmc_name
        pmc.email = payload.email
        pmc.main_contact = payload.main_contact
        pmc.subscription_plan = payload.subscription_plan
        pmc.pms_integration = payload.pms_integration
        pmc.pms_api_key = payload.pms_api_key
        pmc.pms_api_secret = payload.pms_api_secret

        # If you want to allow editing it manually:
        if payload.pms_account_id:
            pmc.pms_account_id = payload.pms_account_id

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


# 🗑️ Delete PMC
@router.delete("/admin/delete-pmc/{pmc_id}")
def delete_pmc(pmc_id: int, request: Request, db: Session = Depends(get_db)):
    require_super(request, db)

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


@router.post("/admin/update-properties")
def update_properties(request: Request, payload: list[dict], db: Session = Depends(get_db)):
    user_role, pmc_obj, _ = get_user_role_and_scope(request, db)
    require_pmc_linked(user_role, pmc_obj)

    try:
        for item in payload:
            prop_id = int(item["id"])
            prop = db.query(Property).filter(Property.id == prop_id).first()
            if not prop:
                continue

            if user_role == "pmc" and prop.pmc_id != pmc_obj.id:
                raise HTTPException(status_code=403, detail="Forbidden property update")

            prop.sandy_enabled = bool(item["sandy_enabled"])

        db.commit()
        return {"success": True}
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})



@router.get("/admin/pmc-properties-json/{pmc_id}")
def get_pmc_properties_json(request: Request, pmc_id: int, db: Session = Depends(get_db)):
    user_role, pmc_obj, _ = get_user_role_and_scope(request, db)
    if user_role == "pmc":
        if not pmc_obj or pmc_obj.id != pmc_id:
            raise HTTPException(status_code=403, detail="Forbidden")
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

