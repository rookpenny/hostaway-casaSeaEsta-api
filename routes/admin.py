from __future__ import annotations

import logging
import os
import requests
import base64
import re
import sqlalchemy as sa
import secrets
import shutil, uuid
import json
from copy import deepcopy

from pathlib import Path

from database import get_db, SessionLocal

from fastapi import APIRouter, Depends, Request, Form, Body, Query, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError

from starlette.status import HTTP_303_SEE_OTHER, HTTP_302_FOUND
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, case, and_, or_, text


from pydantic import BaseModel
from typing import Optional, Dict
from openai import OpenAI
from datetime import datetime, timedelta, date

from models import PMC, Property, ChatSession, ChatMessage, PMCUser, Guide, Upgrade

#from flask import request, render_template, abort, make_response

from utils.github_sync import ensure_repo, sync_files_to_github
from utils.pms_sync import sync_properties, sync_all_integrations
from utils.emailer import send_invite_email, email_enabled
from urllib.parse import urlparse
from utils.ai_summary import generate_and_store_summary
from zoneinfo import ZoneInfo  # Python 3.9+



router = APIRouter()

templates = Jinja2Templates(directory="templates")
logging.basicConfig(level=logging.INFO)

ADMIN_JOB_TOKEN = os.getenv("ADMIN_JOB_TOKEN", "")
SUMMARY_MODEL = os.getenv("OPENAI_SUMMARY_MODEL", "gpt-4o-mini")
ADMIN_IDENTITY_SESSION_KEY = os.getenv("ADMIN_IDENTITY_SESSION_KEY", "admin_email")

ESCALATE_LOW_HEAT = int(os.getenv("ESCALATE_LOW_HEAT", "35"))
ESCALATE_MEDIUM_HEAT = int(os.getenv("ESCALATE_MEDIUM_HEAT", "60"))
ESCALATE_HIGH_HEAT = int(os.getenv("ESCALATE_HIGH_HEAT", "85"))

UPLOAD_DIR = Path("static/uploads/upgrades")       # permanent
TMP_DIR = Path("static/uploads/upgrades/tmp")      # temp
TMP_URL_PREFIX = "/static/uploads/upgrades/tmp"
FINAL_URL_PREFIX = "/static/uploads/upgrades"

ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_MIME_PREFIX = "image/"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8MB

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

# Guest moods (UI dropdown)
GUEST_MOOD_CHOICES = [
    ("", "All guest moods"),
    ("panicked", "😰 Panicked"),
    ("angry", "😡 Angry"),
    ("upset", "😟 Upset"),
    ("confused", "😕 Confused"),
    ("worried", "🥺 Worried"),
    ("stressed", "😮‍💨 Stressed"),
    ("calm", "🙂 Calm"),
]

ALLOWED_GUEST_MOODS = {k for (k, _) in GUEST_MOOD_CHOICES if k}

def normalize_guest_mood(v: str | None) -> str:
    v = (v or "").strip().lower()
    return v if v in ALLOWED_GUEST_MOODS else ""




def batch_message_signals(
    db: Session,
    session_ids: list[int],
) -> dict[int, dict]:
    """
    Single source of truth for mood inputs across list + detail.

    Rules (must match detail):
      - has_urgent: sender in ('guest','user') AND category == 'urgent'
      - has_negative: sender in ('guest','user') AND sentiment == 'negative' AND within last 7 days
      - cnt24/cnt7: counts of ALL messages in window (same as detail)
      - last_guest_text: latest message where sender in ('guest','user') and content not empty
    """
    if not session_ids:
        return {}

    now = datetime.utcnow()
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)

    # counts (all senders, same as your detail route)
    counts_24h = dict(
        db.query(ChatMessage.session_id, func.count(ChatMessage.id))
        .filter(ChatMessage.session_id.in_(session_ids))
        .filter(ChatMessage.created_at >= since_24h)
        .group_by(ChatMessage.session_id)
        .all()
    )

    counts_7d = dict(
        db.query(ChatMessage.session_id, func.count(ChatMessage.id))
        .filter(ChatMessage.session_id.in_(session_ids))
        .filter(ChatMessage.created_at >= since_7d)
        .group_by(ChatMessage.session_id)
        .all()
    )

    # urgent (guest OR user, same as detail)
    urgent_ids = set(
        int(sid) for (sid,) in
        db.query(ChatMessage.session_id)
        .filter(ChatMessage.session_id.in_(session_ids))
        .filter(ChatMessage.sender.in_(("guest", "user")))
        .filter(ChatMessage.category == "urgent")
        .distinct()
        .all()
    )

    # negative (guest OR user, last 7 days, same as detail)
    negative_ids = set(
        int(sid) for (sid,) in
        db.query(ChatMessage.session_id)
        .filter(ChatMessage.session_id.in_(session_ids))
        .filter(ChatMessage.sender.in_(("guest", "user")))
        .filter(ChatMessage.created_at >= since_7d)
        .filter(func.lower(func.coalesce(ChatMessage.sentiment, "")) == "negative")
        .distinct()
        .all()
    )

    # latest guest/user text (for positive override)
    latest_guest_sq = (
        db.query(
            ChatMessage.id.label("id"),
            ChatMessage.session_id.label("session_id"),
            func.row_number()
            .over(
                partition_by=ChatMessage.session_id,
                order_by=(ChatMessage.created_at.desc(), ChatMessage.id.desc()),
            )
            .label("rn"),
        )
        .filter(ChatMessage.session_id.in_(session_ids))
        .filter(ChatMessage.sender.in_(("guest", "user")))
        .filter(func.length(func.coalesce(ChatMessage.content, "")) > 0)
    ).subquery()

    latest_guest_msgs = (
        db.query(ChatMessage.session_id, ChatMessage.content)
        .join(latest_guest_sq, ChatMessage.id == latest_guest_sq.c.id)
        .filter(latest_guest_sq.c.rn == 1)
        .all()
    )
    last_guest_text_by_sid = {int(sid): (content or None) for (sid, content) in latest_guest_msgs}

    out: dict[int, dict] = {}
    for sid in session_ids:
        sid_i = int(sid)
        out[sid_i] = {
            "has_urgent": sid_i in urgent_ids,
            "has_negative": sid_i in negative_ids,
            "cnt24": int(counts_24h.get(sid_i, 0) or 0),
            "cnt7": int(counts_7d.get(sid_i, 0) or 0),
            "last_guest_text": last_guest_text_by_sid.get(sid_i),
        }
    return out




        
# ----------------------------
# OpenAI client (used by summarize + admin chat)
# ----------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
if not OPENAI_API_KEY:
    logging.warning("OPENAI_API_KEY is not set. /admin/chats/{id}/summarize will fail.")

client = OpenAI(api_key=OPENAI_API_KEY)


# ----------------------------
# GitHub helpers
# ----------------------------
def _read_repo_file_text(rel_path: str) -> str:
    repo, repo_root = ensure_repo()
    abs_path = (repo_root / rel_path).resolve()

    # safety: must remain under repo_root
    if not str(abs_path).startswith(str(repo_root.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not abs_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {rel_path}")

    return abs_path.read_text(encoding="utf-8")


def _write_repo_file_text_via_git(rel_path: str, text: str, commit_msg: str) -> None:
    tmp_dir = Path("/tmp/hostscout_ui_edits")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = tmp_dir / f"{uuid.uuid4().hex}.txt"
    tmp_file.write_text(text, encoding="utf-8")

    sync_files_to_github(
        updated_files={rel_path: str(tmp_file)},
        commit_hint=commit_msg,
    )
# ----------------------------
# Chat Stats
# ----------------------------


def _effective_stage_from_dict(d: dict) -> str:
    st = (d.get("reservation_status") or "").strip().lower()

    has_booking = bool(
        d.get("reservation_id")
        or d.get("booking_id")
        or d.get("confirmation_code")
        or d.get("pms_reservation_id")
        or d.get("reservation_confirmed")
    )

    if st in ("post_booking", "booked", "confirmed"):
        return "post_booking"
    if st == "pre_booking" and has_booking:
        return "post_booking"
    return st or "unknown"



# ----------------------------
# The 3 FastAPI routes to use your templates/admin_config_ui.html
# ----------------------------

def _normalize_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        cfg = {}

    a = cfg.get("assistant")
    if not isinstance(a, dict):
        a = {}
        cfg["assistant"] = a

    a.setdefault("name", "Sandy")
    a.setdefault("tone", "luxury")
    a.setdefault("verbosity", "balanced")
    a.setdefault("emoji_level", "light")
    a.setdefault("formality", "polished")
    a.setdefault("avatar_url", "/static/img/sandy.png")
    a.setdefault("style", "Minimal, premium hotel concierge tone. Calm, professional, very short.")
    a.setdefault("extra_instructions", "")

    if not isinstance(a.get("do"), list): a["do"] = []
    if not isinstance(a.get("dont"), list): a["dont"] = []

    v = a.get("voice")
    if not isinstance(v, dict):
        v = {}
        a["voice"] = v

    v.setdefault("welcome_template", "Hi {{guest_name}}! I’m {{assistant_name}}, your stay assistant for {{property_name}}. You can ask about check-in, WiFi, parking, or local tips.")
    v.setdefault("welcome_template_no_name", "Hi there! I’m {{assistant_name}}, your stay assistant for {{property_name}}. Ask me anything about check-in, WiFi, parking, or local recommendations.")
    v.setdefault("offline_message", "I’m currently offline for this property 🌙\n\nFor urgent questions, please contact your host directly via your booking app or email.")
    v.setdefault("fallback_message", "Hmm, I didn’t catch that — could you try again? 🌴")
    v.setdefault("error_message", "Oops! Something went wrong. Please try again in a moment. 🐚")

    if not isinstance(a.get("quick_replies"), list):
        a["quick_replies"] = ["WiFi", "Door code", "Parking", "Check-out time", "Local restaurants", "House rules"]

    return cfg


@router.get("/admin/config-ui", response_class=HTMLResponse)
def admin_config_ui(request: Request, file: str = Query(...), db: Session = Depends(get_db)):
    # ✅ scope gate + sanitize
    file = require_file_in_scope(request, db, file)

    raw = _read_repo_file_text(file)
    try:
        cfg = json.loads(raw or "{}")
    except Exception:
        cfg = {}

    cfg = _normalize_config(cfg)

    is_defaults = file.strip().lower() == "defaults/config.json"
    scope_label = "Defaults" if is_defaults else "Property"

    return templates.TemplateResponse(
        "admin_config_ui.html",
        {
            "request": request,
            "file_path": file,
            "config_json": cfg,
            "is_defaults": is_defaults,
            "scope_label": scope_label,
        },
    )


@router.post("/admin/config-ui/save")
async def admin_config_ui_save(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    file_path = (payload.get("file_path") or "").strip()
    config = payload.get("config") or {}

    if not file_path:
        return JSONResponse({"ok": False, "error": "Missing file_path"}, status_code=400)

    file_path = require_file_in_scope(request, db, file_path)

    if not isinstance(config, dict):
        return JSONResponse({"ok": False, "error": "Invalid config payload"}, status_code=400)

    config = _normalize_config(config)
    text = json.dumps(config, indent=2, ensure_ascii=False) + "\n"

    _write_repo_file_text_via_git(
        rel_path=file_path,
        text=text,
        commit_msg=f"Update config via UI: {file_path}",
    )
    return {"ok": True}


@router.post("/admin/config-ui/reset")
async def admin_config_ui_reset(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    file_path = (payload.get("file_path") or "").strip()
    if not file_path:
        return JSONResponse({"ok": False, "error": "Missing file_path"}, status_code=400)

    file_path = require_file_in_scope(request, db, file_path)

    defaults_raw = _read_repo_file_text("defaults/config.json")
    try:
        defaults_cfg = json.loads(defaults_raw or "{}")
    except Exception:
        defaults_cfg = {}
    defaults_cfg = _normalize_config(defaults_cfg)

    # If resetting defaults file itself, just return it
    if file_path.strip().lower() == "defaults/config.json":
        return {"ok": True, "config": defaults_cfg}

    text = json.dumps(defaults_cfg, indent=2, ensure_ascii=False) + "\n"

    _write_repo_file_text_via_git(
        rel_path=file_path,
        text=text,
        commit_msg=f"Reset config to defaults: {file_path}",
    )
    return {"ok": True, "config": defaults_cfg}





# ----------------------------
# JSON CONFIG Helpers
# ----------------------------
def deep_get(d: dict, path: str, default=None):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur

def deep_set(d: dict, path: str, value):
    cur = d
    parts = path.split(".")
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value

def apply_defaults(cfg: dict) -> dict:
    cfg = deepcopy(cfg or {})
    for sec in CONFIG_FORM:
        for f in sec["fields"]:
            if deep_get(cfg, f["path"], None) is None:
                deep_set(cfg, f["path"], deepcopy(f.get("default")))
    return cfg


def parse_field_value(field, raw: str | None):
    t = field["type"]

    if t == "toggle":
        return raw is not None  # checkbox present

    if t == "number":
        s = (raw or "").strip()
        if s == "":
            return field.get("default", 0)
        try:
            return int(s)
        except Exception:
            return field.get("default", 0)

    if t == "list_text":
        # textarea lines -> list of strings
        lines = (raw or "").splitlines()
        out = [ln.strip() for ln in lines if ln.strip()]
        return out

    # text / textarea / select
    return (raw or "").strip()


ALLOWED_TEMPLATE_VARS = {"guest_name", "assistant_name", "property_name"}

def validate_config(cfg: dict) -> list[str]:
    errors = []

    # escalation ordering
    try:
        low = int(deep_get(cfg, "escalation.low", 0))
        med = int(deep_get(cfg, "escalation.medium", 0))
        high = int(deep_get(cfg, "escalation.high", 0))
        if not (0 <= low <= 100 and 0 <= med <= 100 and 0 <= high <= 100):
            errors.append("Escalation thresholds must be between 0 and 100.")
        if not (low < med < high):
            errors.append("Escalation thresholds must satisfy low < medium < high.")
    except Exception:
        errors.append("Escalation thresholds are invalid.")

    # template variable safety

    for p in [
        "assistant.voice.welcome_template",
        "assistant.voice.welcome_template_no_name",
        "assistant.voice.offline_message",
        "assistant.voice.fallback_message",
        "assistant.voice.error_message",
    ]:
        txt = deep_get(cfg, p, "") or ""
        found = set(re.findall(r"{{\s*([a-zA-Z0-9_]+)\s*}}", txt))
        bad = sorted([v for v in found if v not in ALLOWED_TEMPLATE_VARS])
        if bad:
            errors.append(f"{p} uses unknown template vars: {', '.join(bad)}")

    return errors



# ----------------------------
# Pydantic classes
# ----------------------------

class ProfileUpdatePayload(BaseModel):
    full_name: Optional[str] = None
    timezone: Optional[str] = None  # UI-only unless you add a DB column


class InviteTeamPayload(BaseModel):
    email: str
    role: str = "staff"
    full_name: Optional[str] = None

class UpdateTeamMemberPayload(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None

class NotificationPrefsPayload(BaseModel):
    prefs: Dict[str, bool]



def delete_local_upgrade_image(image_url: str) -> None:
    """
    Delete /static/uploads/upgrades/<filename> only (ignore external URLs).
    """
    if not image_url:
        return
    if image_url.startswith(("http://", "https://")):
        return
    if not image_url.startswith("/static/uploads/upgrades/"):
        return

    rel = image_url.lstrip("/")  # static/uploads/...
    p = Path(rel)
    _safe_unlink(p)



def fetch_dashboard_chat_sessions(
    db,
    *,
    pmc_id=None,
    property_id=None,
    status=None,
    action_priority=None,
    q=None,
    limit=200,
):
    sql = text("""
    WITH base AS (
      SELECT
        cs.id,
        cs.property_id,
        cs.last_activity_at,
        cs.is_resolved,
        cs.reservation_status,
        cs.escalation_level,
        cs.assigned_to,
        cs.heat_score,
        cs.guest_name,
        cs.source,
        cs.pms_reservation_id,
        cs.arrival_date,
        cs.departure_date,
        p.property_name,
        p.pmc_id
      FROM chat_sessions cs
      JOIN properties p ON p.id = cs.property_id
      WHERE 1=1
        AND (:pmc_id IS NULL OR p.pmc_id = :pmc_id)
        AND (:property_id IS NULL OR cs.property_id = :property_id)
        AND (:status IS NULL OR cs.reservation_status = :status)

        AND (
          :action_priority IS NULL
          OR (
            (:action_priority = 'urgent' AND cs.heat_score >= 90)
            OR (:action_priority = 'high' AND cs.heat_score >= 80 AND cs.heat_score < 90)
            OR (:action_priority = 'normal' AND cs.heat_score >= 50 AND cs.heat_score < 80)
            OR (:action_priority = 'low' AND (cs.heat_score < 50 OR cs.heat_score IS NULL))
          )
        )
    )
    SELECT
      b.*,
      lg.guest_mood,
      lg.guest_mood_confidence,
      lg.guest_sentiment,
      lg.guest_sentiment_data,
      lm.last_message,
      lm.last_sender
    FROM base b

    LEFT JOIN LATERAL (
      SELECT
        cm.sentiment_data->>'mood' AS guest_mood,
        NULLIF(cm.sentiment_data->>'confidence','')::int AS guest_mood_confidence,
        cm.sentiment AS guest_sentiment,
        cm.sentiment_data AS guest_sentiment_data
      FROM chat_messages cm
      WHERE cm.session_id = b.id
        AND cm.sender IN ('guest','user')
      ORDER BY cm.created_at DESC
      LIMIT 1
    ) lg ON TRUE

    LEFT JOIN LATERAL (
      SELECT
        cm2.content AS last_message,
        cm2.sender  AS last_sender
      FROM chat_messages cm2
      WHERE cm2.session_id = b.id
      ORDER BY cm2.created_at DESC
      LIMIT 1
    ) lm ON TRUE

    WHERE 1=1
      AND (
        :q IS NULL
        OR b.property_name ILIKE '%' || :q || '%'
        OR coalesce(lm.last_message,'') ILIKE '%' || :q || '%'
      )

    ORDER BY b.last_activity_at DESC NULLS LAST
    LIMIT :limit
    """)

    params = {
        "pmc_id": pmc_id,
        "property_id": property_id,
        "status": status,
        "action_priority": (action_priority or None),
        "q": q,
        "limit": int(limit),
    }

    return db.execute(sql, params).mappings().all()


# ----------------------------
# Action priority + guest mood helpers (MODULE-LEVEL)
# Used by /admin/dashboard + /admin/chats + partials
# ----------------------------


def persist_session_triage_fields(
    db: Session,
    sess: ChatSession,
    *,
    emotional_signals: list[str],
    action_priority: str,
    guest_mood: str | None = None,
    guest_mood_confidence: int | None = None,
) -> bool:
    """
    Persists computed fields to ChatSession if the columns exist.
    Returns True if anything changed.
    """
    changed = False

    # emotional_signals JSONB array
    if hasattr(sess, "emotional_signals"):
        new_val = emotional_signals or []
        old_val = getattr(sess, "emotional_signals", None) or []
        if old_val != new_val:
            setattr(sess, "emotional_signals", new_val)
            changed = True

    # action_priority
    if hasattr(sess, "action_priority"):
        ap = normalize_action_priority(action_priority)
        if getattr(sess, "action_priority", None) != ap:
            setattr(sess, "action_priority", ap)
            changed = True

    # guest mood (optional)
    if guest_mood is not None and hasattr(sess, "guest_mood"):
        if getattr(sess, "guest_mood", None) != guest_mood:
            setattr(sess, "guest_mood", guest_mood)
            changed = True

    if guest_mood_confidence is not None and hasattr(sess, "guest_mood_confidence"):
        if getattr(sess, "guest_mood_confidence", None) != int(guest_mood_confidence):
            setattr(sess, "guest_mood_confidence", int(guest_mood_confidence))
            changed = True

    if changed:
        sess.updated_at = datetime.utcnow()
        db.add(sess)

    return changed

def normalize_action_priority(value: str | None) -> str | None:
    """
    Normalizes different priority label sets into the canonical:
    urgent | high | normal | low
    """
    if not value:
        return None
    v = value.strip().lower()
    mapping = {
        # legacy/alternate labels
        "critical": "urgent",
        "urgent": "urgent",

        "attention": "high",
        "high": "high",

        "medium": "normal",
        "normal": "normal",

        "routine": "low",
        "low": "low",
    }
    return mapping.get(v, v)


def action_priority_from_heat(heat: int) -> str:
    # Canonical output: urgent/high/normal/low
    if heat >= 80:
        return "urgent"
    if heat >= 60:
        return "high"
    if heat >= 40:
        return "normal"
    return "low"


def bump_priority(base: str, bump_to: str) -> str:
    rank = {"low": 0, "normal": 1, "high": 2, "urgent": 3}
    return bump_to if rank.get(bump_to, 0) > rank.get(base, 0) else base




POSITIVE_WORDS = {
    "love", "like", "loved", "liked",
    "haha", "lol", "lmao",
    "amazing", "awesome", "great", "perfect",
    "thanks", "thx", "appreciate",
}
POSITIVE_PHRASES = {"thank you"}
POSITIVE_EMOJIS = {"😂", "🤣"}

_WORD_RE = re.compile(r"\b(" + "|".join(map(re.escape, POSITIVE_WORDS)) + r")\b", re.IGNORECASE)

def _looks_positive(text: str | None) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if _WORD_RE.search(t):
        return True
    if any(p in t for p in POSITIVE_PHRASES):
        return True
    if any(e in t for e in POSITIVE_EMOJIS):
        return True
    return False



def derive_guest_mood(
    has_urgent: bool,
    has_negative: bool,
    cnt24: int,
    cnt7: int,
    status_val: str | None,
    last_guest_text: str | None = None,   # ✅ new optional input
) -> list[str]:
    """
    Returns a list of emotional signals, ordered most important first.
    Output values are lowercase to match your filters.
    """
    signals: list[str] = []
    status = (status_val or "").strip().lower()

    is_positive = _looks_positive(last_guest_text)

    if has_urgent:
        signals.append("panicked")
    if has_negative and "panicked" not in signals:
        signals.append("upset")

    # High volume can imply confusion/worry
    # ✅ BUT: don't label as confused if the last message looks positive
    if cnt24 >= 8 and "panicked" not in signals and not is_positive:
        signals.append("confused")
    elif cnt7 >= 25 and "panicked" not in signals and not is_positive:
        signals.append("worried")

    # Optional status-based signal
    if status == "active" and (has_urgent or has_negative):
        signals.append("stressed")

    # ✅ Positive override: if we only had volume-based confusion, make it calm instead
    if is_positive and signals and signals[0] in {"confused", "worried"} and not (has_urgent or has_negative):
        signals = ["calm"]

    if not signals:
        signals.append("calm")

    # de-dupe, keep order, cap to 2
    out: list[str] = []
    seen = set()
    for s in signals:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out[:2]



def compute_action_priority(
    heat: int,
    signals: list[str],
    has_urgent: bool,
    has_negative: bool,
) -> str:
    """
    Canonical output: urgent/high/normal/low
    Max of heat priority and signal priority.
    """
    ap = action_priority_from_heat(int(heat or 0))

    if has_urgent or ("panicked" in (signals or [])):
        ap = bump_priority(ap, "urgent")
    elif has_negative or ("angry" in (signals or [])) or ("upset" in (signals or [])):
        ap = bump_priority(ap, "high")

    return ap


def apply_action_priority_filter(qry, ap: str):
    """
    Applies dashboard priority filter tiers based on heat_score.
    This is your existing behavior, just moved to module level.
    """
    ap = (ap or "").strip().lower()
    if not ap:
        return qry
    if ap == "urgent":
        return qry.filter(ChatSession.heat_score >= 90)
    if ap == "high":
        return qry.filter(ChatSession.heat_score >= 80, ChatSession.heat_score < 90)
    if ap == "normal":
        return qry.filter(ChatSession.heat_score >= 50, ChatSession.heat_score < 80)
    if ap == "low":
        return qry.filter(or_(ChatSession.heat_score < 50, ChatSession.heat_score.is_(None)))
    return qry



# ----------------------------
# Flask-ish example
# ----------------------------

@router.get("/admin/chats/partial/detail", response_class=HTMLResponse)
def chat_detail_partial(
    request: Request,
    session_id: int,
    db: Session = Depends(get_db),
):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401)

    sess = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Chat not found")

    property_obj = db.query(Property).filter(Property.id == sess.property_id).first()

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == sess.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    # --------------------------------------------------
    # Message-derived signals
    # --------------------------------------------------
    now = datetime.utcnow()

    has_urgent = (
        db.query(ChatMessage.id)
        .filter(
            ChatMessage.session_id == sess.id,
            ChatMessage.sender.in_(("guest", "user")),
            ChatMessage.category == "urgent",
        )
        .first()
        is not None
    )

    cutoff_7d = now - timedelta(days=7)
    has_negative = (
        db.query(ChatMessage.id)
        .filter(
            ChatMessage.session_id == sess.id,
            ChatMessage.sender.in_(("guest", "user")),
            ChatMessage.created_at >= cutoff_7d,
            func.lower(func.coalesce(ChatMessage.sentiment, "")) == "negative",
        )
        .first()
        is not None
    )

    since_24h = now - timedelta(hours=24)
    cnt24 = int(
        db.query(func.count(ChatMessage.id))
        .filter(
            ChatMessage.session_id == sess.id,
            ChatMessage.created_at >= since_24h,
        )
        .scalar()
        or 0
    )

    since_7d = now - timedelta(days=7)
    cnt7 = int(
        db.query(func.count(ChatMessage.id))
        .filter(
            ChatMessage.session_id == sess.id,
            ChatMessage.created_at >= since_7d,
        )
        .scalar()
        or 0
    )

    status_val = (sess.reservation_status or "pre_booking").strip().lower() or "pre_booking"

    # --------------------------------------------------
    # Last guest text (for positive override)
    # --------------------------------------------------
    last_guest_text = None
    for m in reversed(messages):
        if m.sender in ("guest", "user") and (m.content or "").strip():
            last_guest_text = m.content
            break

    # --------------------------------------------------
    # Emotional signals (guest mood)
    # --------------------------------------------------
    emotional_signals = derive_guest_mood(
        has_urgent=has_urgent,
        has_negative=has_negative,
        cnt24=cnt24,
        cnt7=cnt7,
        status_val=status_val,
        last_guest_text=last_guest_text,  # ✅ positivity-aware
    )

    guest_mood_val = emotional_signals[0] if emotional_signals else None

    # --------------------------------------------------
    # Heat computation (same logic as dashboard)
    # --------------------------------------------------
    raw_heat = (
        (50 if has_urgent else 0)
        + (25 if has_negative else 0)
        + min(25, cnt24 * 5)
        + min(10, cnt7)
    )
    raw_heat = min(100, raw_heat)

    boosted = raw_heat
    if has_urgent:
        boosted = int(boosted * 1.3)
    if has_negative:
        boosted = int(boosted * 1.15)
    if status_val == "active":
        boosted = int(boosted * 1.1)

    heat = decay_heat(min(100, boosted), sess.last_activity_at)

    # --------------------------------------------------
    # Action priority
    # --------------------------------------------------
    action_priority_val = compute_action_priority(
        heat=heat,
        signals=emotional_signals,
        has_urgent=has_urgent,
        has_negative=has_negative,
    )

    # --------------------------------------------------
    # View model
    # --------------------------------------------------
    session_vm = {
        "id": sess.id,
        "property_id": sess.property_id,
        "guest_name": sess.guest_name,
        "assigned_to": sess.assigned_to,
        "reservation_status": sess.reservation_status,
        "escalation_level": sess.escalation_level,
        "is_resolved": bool(sess.is_resolved),

        # ✅ keys template expects
        "action_priority": action_priority_val,
        "emotional_signals": emotional_signals,
        "guest_mood": guest_mood_val,  # optional but handy

        # optional extras
        "internal_note": getattr(sess, "internal_note", None),
        "ai_summary": getattr(sess, "ai_summary", None),
        "ai_summary_updated_at": getattr(sess, "ai_summary_updated_at", None),

        # booking fields
        "reservation_id": getattr(sess, "reservation_id", None),
        "booking_id": getattr(sess, "booking_id", None),
        "confirmation_code": getattr(sess, "confirmation_code", None),
        "pms_reservation_id": getattr(sess, "pms_reservation_id", None),
        "reservation_confirmed": getattr(sess, "reservation_confirmed", None),
    }

    return templates.TemplateResponse(
        "partials/chat_detail_panel.html",
        {
            "request": request,
            "session": session_vm,
            "property": property_obj,
            "messages": messages,
            "property_name_by_id": (
                {property_obj.id: property_obj.property_name}
                if property_obj
                else {}
            ),
        },
    )



# ----------------------------
# Auth / scope helpers
# ----------------------------
def get_current_admin_identity(request: Request) -> Optional[str]:
    # 1) Auth middleware scope user
    try:
        user = request.scope.get("user")
        if user and getattr(user, "is_authenticated", False):
            for attr in ("email", "username", "name"):
                val = getattr(user, attr, None)
                if val and str(val).strip():
                    return str(val).strip().lower()
    except Exception:
        pass

    # 2) Explicit session key
    try:
        sess_val = request.session.get(ADMIN_IDENTITY_SESSION_KEY)
        if sess_val and str(sess_val).strip():
            return str(sess_val).strip().lower()
    except Exception:
        pass

    # 3) Existing Google login flow: session["user"]["email"]
    try:
        sess_user = request.session.get("user")
        if isinstance(sess_user, dict):
            email = (sess_user.get("email") or "").strip()
            if email:
                return email.lower()
    except Exception:
        pass

    # 4) Header fallback
    try:
        hdr = request.headers.get("x-admin-email") or request.headers.get("x-admin-user")
        if hdr and hdr.strip():
            return hdr.strip().lower()
    except Exception:
        pass

    return None


def is_super_admin(email: Optional[str]) -> bool:
    if not email:
        return False

    allow = os.getenv("ADMIN_EMAILS", "")
    if allow.strip():
        allowed = {e.strip().lower() for e in allow.split(",") if e.strip()}
        return email.lower() in allowed

    # fallback (set ADMIN_EMAILS in env and remove this once stable)
    return email.lower() in {"corbett.jarrod@gmail.com"}

def delete_temp_upgrade_image(tmp_key: str) -> None:
    """
    Deletes a temp upgrade image safely.
    tmp_key should be a filename only (no paths).
    """
    if not tmp_key:
        return

    # Prevent path traversal
    safe_key = Path(tmp_key).name
    path = TMP_DIR / safe_key

    try:
        if path.exists() and path.is_file():
            path.unlink()
    except Exception as e:
        print("[delete_temp_upgrade_image] failed:", e)


@router.post("/admin/upgrades/ajax/delete-temp-image")
async def upgrades_delete_temp_image(payload: dict):
    tmp_key = (payload.get("tmp_key") or "").strip()
    delete_temp_upgrade_image(tmp_key)
    return {"ok": True}

def get_user_role_and_scope(request: Request, db: Session):
    """
    Returns:
      (user_role, pmc_obj, pmc_user, billing_status, needs_payment)

    - user_role: "super" | "pmc"
    - pmc_obj: PMC | None
    - pmc_user: PMCUser | None
    - billing_status: "active" | "pending" | "past_due" | ... | None
    - needs_payment: bool (True if PMC exists but is not allowed to "begin")
    """
    email = get_current_admin_identity(request)

    if is_super_admin(email):
        return "super", None, None, None, False

    if not email:
        return "pmc", None, None, None, False

    email_l = (email or "").strip().lower()

    # 1) Prefer explicit PMCUser membership
    pmc_user = (
        db.query(PMCUser)
        .filter(func.lower(PMCUser.email) == email_l, PMCUser.is_active == True)
        .first()
    )
    
    # ✅ NEW: DB-driven superuser override
    if pmc_user and bool(getattr(pmc_user, "is_superuser", False)):
        return "super", None, pmc_user, None, False

    pmc = None
    if pmc_user:
        pmc = db.query(PMC).filter(PMC.id == pmc_user.pmc_id).first()
    else:
        # 2) Fallback: PMC owner email on PMC table
        pmc = db.query(PMC).filter(func.lower(PMC.email) == email_l).first()

    if not pmc:
        return "pmc", None, None, None, False

    # --- Billing gating ---
    billing_status = (getattr(pmc, "billing_status", None) or "pending").strip().lower()

    # "Begin" requires both:
    # - billing_status == active
    # - pmc.active == True
    is_paid_and_active = (billing_status == "active") and bool(getattr(pmc, "active", False))
    needs_payment = not is_paid_and_active

    return "pmc", pmc, pmc_user, billing_status, needs_payment





@router.get("/admin/settings/team/table", response_class=HTMLResponse)
def team_table_rows(request: Request, db: Session = Depends(get_db)):
    user_role, pmc_obj, pmc_user, *_ = get_user_role_and_scope(request, db)
    require_pmc_linked(user_role, pmc_obj)

    me_email = (get_current_admin_identity(request) or "").strip().lower()

    team_members = (
        db.query(PMCUser)
        .filter(PMCUser.pmc_id == pmc_obj.id)
        .order_by(func.lower(PMCUser.email).asc())
        .all()
    )

    return templates.TemplateResponse(
        "admin/_team_table_rows.html",
        {
            "request": request,
            "team_members": team_members,
            "user_email": me_email,
        },
    )




def require_super(request: Request, db: Session):
    role, *_ = get_user_role_and_scope(request, db)
    if role != "super":
        raise HTTPException(status_code=403, detail="Forbidden")


def require_pmc_linked(user_role: str, pmc_obj: Optional[PMC]):
    if user_role == "pmc" and not pmc_obj:
        raise HTTPException(status_code=403, detail="PMC account not linked")


def enforce_assignee_in_pmc(request: Request, db: Session, assigned_to: str):
    assigned_to = (assigned_to or "").strip().lower()
    if not assigned_to:
        return

    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)

    if user_role == "super":
        return

    if user_role == "pmc" and not pmc_obj:
        raise HTTPException(status_code=403, detail="PMC account not linked")

    ok = (
        db.query(PMCUser)
        .filter(
            PMCUser.pmc_id == pmc_obj.id,
            sa.func.lower(PMCUser.email) == assigned_to,
            PMCUser.is_active == True,
        )
        .first()
    )
    if not ok:
        raise HTTPException(status_code=403, detail="Assignee not in your PMC team")


def require_session_in_scope(request: Request, db: Session, session_id: int) -> ChatSession:
    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)
    require_pmc_linked(user_role, pmc_obj)

    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Not found")

    if user_role == "pmc":
        prop = db.query(Property).filter(Property.id == session.property_id).first()
        if not prop or prop.pmc_id != pmc_obj.id:
            raise HTTPException(status_code=403, detail="Forbidden")

    return session


def require_file_in_scope(request: Request, db: Session, file_path: str) -> str:
    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)
    require_pmc_linked(user_role, pmc_obj)

    file_path = (file_path or "").strip().lstrip("/").strip()
    if ".." in file_path.split("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    # ✅ Allow defaults for everyone who is logged in + scoped (or super)
    if file_path.startswith("defaults/"):
        return file_path

    if user_role == "super":
        return file_path

    props = db.query(Property).filter(Property.pmc_id == pmc_obj.id).all()
    for p in props:
        base = (getattr(p, "data_folder_path", None) or "").strip().strip("/")
        if base and (file_path == base or file_path.startswith(base + "/")):
            return file_path

    raise HTTPException(status_code=403, detail="Forbidden file")




@router.get("/admin/settings/team", response_class=HTMLResponse)
def get_team_table(request: Request, db: Session = Depends(get_db)):
    user_role, pmc_obj, pmc_user, *_ = get_user_role_and_scope(request, db)
    require_pmc_linked(user_role, pmc_obj)

    me_email = (get_current_admin_identity(request) or "").strip().lower()

    team_members = (
        db.query(PMCUser)
        .filter(PMCUser.pmc_id == pmc_obj.id)
        .order_by(func.lower(PMCUser.email).asc())
        .all()
    )

    # Return the same rows partial your UI already uses
    return templates.TemplateResponse(
        "admin/_team_table_rows.html",
        {
            "request": request,
            "team_members": team_members,
            "user_email": me_email,
        },
    )



# ----------------------------
# SYNC Properties
# ----------------------------
@router.post("/admin/pmcs/{pmc_id}/sync-properties")
def admin_sync_properties(pmc_id: int, request: Request, db: Session = Depends(get_db)):
    require_super(request, db)

    try:
        count, synced_at = sync_properties_for_pmc(db, pmc_id=pmc_id)
        return JSONResponse({"success": True, "message": f"Synced {count} properties", "synced_at": synced_at})
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] Failed to sync for pmc_id={pmc_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)




# ----------------------------
# Reusable “scope filter” helper
# ----------------------------

def apply_chat_scope(q, user_role, pmc_obj):
    """
    q is a SQLAlchemy query already joined to Property (or can be).
    Superuser: no filter
    PMC: only their properties
    """
    if user_role == "pmc":
        require_pmc_linked(user_role, pmc_obj)
        q = q.filter(Property.pmc_id == pmc_obj.id)
    return q



# ----------------------------
# Delete a team member (owner/admin only)
# ----------------------------
@router.delete("/admin/settings/team/{member_id}")
def delete_team_member(member_id: int, request: Request, db: Session = Depends(get_db)):
    _, pmc_obj, me = require_team_admin(request, db)

    member = (
        db.query(PMCUser)
        .filter(PMCUser.id == member_id, PMCUser.pmc_id == pmc_obj.id)
        .first()
    )
    if not member:
        raise HTTPException(status_code=404, detail="User not found")

    me_role = (me.role or "").lower()
    member_role = (member.role or "").lower()

    # Never allow deleting yourself
    if member.id == me.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    # Only owners can delete owners
    if member_role == "owner" and me_role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can delete an owner")

    # Optional: prevent deleting the last owner
    if member_role == "owner":
        owner_count = (
            db.query(PMCUser)
            .filter(PMCUser.pmc_id == pmc_obj.id, PMCUser.role == "owner", PMCUser.is_active == True)
            .count()
        )
        if owner_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot delete the last owner")

    db.delete(member)
    db.commit()
    return {"ok": True}


# ----------------------------
# Invite a team member (PMC owner/admin only)
# ----------------------------

logger = logging.getLogger(__name__)

@router.post("/admin/settings/team/invite")
def invite_team_member(request: Request, payload: InviteTeamPayload, db: Session = Depends(get_db)):
    user_role, pmc_obj, me = require_team_admin(request, db)

    email = (payload.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email required")

    role = (payload.role or "staff").strip().lower()
    if role not in TEAM_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role: {role}")

    u = PMCUser(
        pmc_id=pmc_obj.id,
        email=email,
        full_name=(payload.full_name or None),
        role=role,
        is_active=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    created = False
    try:
        db.add(u)
        db.commit()
        created = True
    except IntegrityError:
        db.rollback()
        # already exists
        u = db.query(PMCUser).filter(PMCUser.pmc_id == pmc_obj.id, func.lower(PMCUser.email) == email).first()

    # send email (do not crash the request)
    email_sent = False
    email_error = None
    try:
        invited_by = (me.full_name or me.email) if me else "Your team"
        send_invite_email(to_email=email, invited_by=invited_by, pmc_name=(pmc_obj.pmc_name or "your team"))
        email_sent = True
    except Exception as e:
        email_error = str(e)
        logger.exception("Invite email failed")

    msg_bits = []
    msg_bits.append("Member added." if created else "Member already existed.")
    msg_bits.append("Invite email sent." if email_sent else "Email NOT sent (check Resend domain / EMAIL_FROM).")

    return {
        "ok": True,
        "member_id": u.id if u else None,
        "email_sent": email_sent,
        "message": " ".join(msg_bits),
        "email_error": email_error,   # optional: helpful while debugging
    }



# ----------------------------
# Update profile (PMC users only)
# ----------------------------
@router.post("/admin/settings/profile")
def update_profile(
    request: Request,
    payload: ProfileUpdatePayload,
    db: Session = Depends(get_db),
):
    email = get_current_admin_identity(request)
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")

    email_l = email.strip().lower()

    u = (
        db.query(PMCUser)
        .filter(func.lower(PMCUser.email) == email_l, PMCUser.is_active == True)
        .first()
    )
    if not u:
        raise HTTPException(status_code=404, detail="Profile not found (PMC users only)")

    # Full name
    if payload.full_name is not None:
        full = (payload.full_name or "").strip()
        u.full_name = full or None

    # Timezone (requires PMCUser.timezone column + DB column)
    if payload.timezone is not None:
        tz = (payload.timezone or "").strip()
        if tz == "":
            u.timezone = None
        else:
            # validate timezone string
            try:
                ZoneInfo(tz)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid timezone")
            u.timezone = tz

    u.updated_at = datetime.utcnow()
    db.add(u)
    db.commit()

    return {"ok": True}


# ----------------------------
# Update a team member (PMC owner/admin only)
# ----------------------------
@router.post("/admin/settings/team/{member_id}")
def update_team_member(
    member_id: int,
    request: Request,
    payload: UpdateTeamMemberPayload,
    db: Session = Depends(get_db),
):
    # returns: (user_role, pmc_obj, pmc_user)
    _, pmc_obj, me = require_team_admin(request, db)

    member = (
        db.query(PMCUser)
        .filter(PMCUser.id == member_id, PMCUser.pmc_id == pmc_obj.id)
        .first()
    )
    if not member:
        raise HTTPException(status_code=404, detail="User not found")

    me_role = (me.role or "").lower()
    member_role = (member.role or "").lower()

    # Prevent modifying the last/any owner unless you're an owner
    if member_role == "owner" and me_role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can modify another owner")

    # Prevent self-disable (common footgun)
    if payload.is_active is not None and member.id == me.id and bool(payload.is_active) is False:
        raise HTTPException(status_code=400, detail="You cannot disable your own account")

    # Role update
    if payload.role is not None:
        new_role = (payload.role or "").strip().lower()
        if new_role not in TEAM_ROLES:
            raise HTTPException(status_code=400, detail=f"Invalid role: {new_role}")

        # Admin cannot promote/demote to/from owner
        if me_role != "owner" and (new_role == "owner" or member_role == "owner"):
            raise HTTPException(status_code=403, detail="Only an owner can assign the owner role")

        member.role = new_role

    # Active toggle
    if payload.is_active is not None:
        # Don’t allow disabling an owner unless you're an owner
        if member_role == "owner" and me_role != "owner" and bool(payload.is_active) is False:
            raise HTTPException(status_code=403, detail="Only an owner can disable an owner")
        member.is_active = bool(payload.is_active)

    member.updated_at = datetime.utcnow()
    db.add(member)
    db.commit()
    return {"ok": True}


# ----------------------------
# Save notification preferences (PMC user)
# ----------------------------
@router.post("/admin/settings/notifications")
def save_notification_prefs(
    request: Request,
    payload: NotificationPrefsPayload,
    db: Session = Depends(get_db),
):
    email = get_current_admin_identity(request)
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")

    email_l = email.strip().lower()

    u = (
        db.query(PMCUser)
        .filter(func.lower(PMCUser.email) == email_l, PMCUser.is_active == True)
        .first()
    )
    if not u:
        raise HTTPException(status_code=404, detail="User not found (PMC users only)")

    prefs = payload.prefs or {}

    # Optional: whitelist known keys so random junk doesn't get stored
    allowed = {"guest_messages", "maintenance_assigned", "turnover_due"}
    cleaned = {k: bool(v) for k, v in prefs.items() if str(k) in allowed}

    u.notification_prefs = cleaned
    u.updated_at = datetime.utcnow()
    db.add(u)
    db.commit()

    return {"ok": True, "prefs": cleaned}



# ----------------------------
# Heat / escalation helpers
# ----------------------------
def decay_heat(heat_value: int, last_activity_at: Optional[datetime]) -> int:
    if not last_activity_at:
        return heat_value

    try:
        now = datetime.utcnow()
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
    if not ai_summary:
        return None

    text = ai_summary.strip()
    m = re.search(r"\*\*Recommended next action\*\*\s*", text, flags=re.IGNORECASE)
    if not m:
        return None

    tail = text[m.end():].lstrip()
    stop = re.search(r"\n\s*\*\*[^*]+\*\*\s*", tail)
    if stop:
        tail = tail[:stop.start()].strip()

    if not tail:
        return None

    item = re.search(r"^\s*(?:[-*]|[0-9]+\.)\s+(.+)$", tail, flags=re.MULTILINE)
    if item:
        return item.group(1).strip()[:140]

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
    if heat >= ESCALATE_LOW_HEAT:
        return "low"
    return None


def escalation_rank(level: Optional[str]) -> int:
    order = {None: 0, "": 0, "low": 1, "medium": 2, "high": 3}
    return order.get((level or "").lower(), 0)

def derive_signals(has_urgent: bool, has_negative: bool, cnt24: int, cnt7: int, status_val: str) -> list[str]:
    # Backward-compatible wrapper. Keep until all callers migrate.
    return derive_guest_mood(
        has_urgent=has_urgent,
        has_negative=has_negative,
        cnt24=cnt24,
        cnt7=cnt7,
        status_val=status_val,
        last_guest_text=None,
    )


# ----------------------------
# Chats list + actions
# ----------------------------
@router.get("/admin/chats", response_class=HTMLResponse)
def admin_chats(
    request: Request,
    db: Session = Depends(get_db),

    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    q: Optional[str] = Query(None),

    pmc_id: Optional[str] = Query(None),
    property_id: Optional[str] = Query(None),

    mine: Optional[int] = Query(None),
    assigned_to: Optional[str] = Query(None),

    # canonical
    guest_mood: Optional[str] = Query(None),

    # backward compat
    signals_filter: Optional[str] = Query(None),
):
    # --------------------------------------------------
    # Auth + scope
    # --------------------------------------------------
    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)
    if user_role == "pmc" and not pmc_obj:
        raise HTTPException(status_code=403, detail="PMC account not linked")

    me_email = get_current_admin_identity(request)

    def to_int(v: Optional[str]) -> Optional[int]:
        s = (v or "").strip()
        return int(s) if s.isdigit() else None

    pmc_id_int = to_int(pmc_id)
    property_id_int = to_int(property_id)

    mood = normalize_guest_mood(guest_mood or signals_filter)

    # --------------------------------------------------
    # Dropdown data
    # --------------------------------------------------
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

    # --------------------------------------------------
    # Base query
    # --------------------------------------------------
    q_base = (
        db.query(ChatSession, Property)
        .join(Property, ChatSession.property_id == Property.id)
    )

    # scope
    if user_role == "pmc":
        q_base = q_base.filter(Property.pmc_id == pmc_obj.id)
    elif pmc_id_int:
        q_base = q_base.filter(Property.pmc_id == pmc_id_int)

    if property_id_int:
        if allowed_property_ids and property_id_int not in allowed_property_ids:
            raise HTTPException(status_code=403)
        q_base = q_base.filter(ChatSession.property_id == property_id_int)

    if status in {"pre_booking", "post_booking", "active", "post_stay"}:
        q_base = q_base.filter(ChatSession.reservation_status == status)

    # assignment
    effective_assignee = (assigned_to or "").strip()
    if mine and me_email:
        effective_assignee = me_email

    if effective_assignee:
        q_base = q_base.filter(
            func.lower(func.coalesce(ChatSession.assigned_to, "")) ==
            effective_assignee.lower()
        )

    rows = (
        q_base
        .order_by(ChatSession.last_activity_at.desc(), ChatSession.id.desc())
        .limit(200)
        .all()
    )

    session_ids = [int(s.id) for (s, _) in rows]
    if not session_ids:
        return templates.TemplateResponse(
            "admin_chats.html",
            {
                "request": request,
                "sessions": [],
                "filters": {
                    "status": status,
                    "priority": priority,
                    "q": q,
                    "pmc_id": (str(pmc_obj.id) if user_role == "pmc" else (pmc_id or "")),
                    "property_id": property_id or "",
                    "mine": bool(mine),
                    "assigned_to": effective_assignee,
                    "guest_mood": mood,
                },
                "guest_mood_choices": GUEST_MOOD_CHOICES,
                "pmcs": pmcs,
                "properties": properties,
                "user_role": user_role,
            },
        )

    # --------------------------------------------------
    # Shared message-derived inputs (single source of truth)
    # --------------------------------------------------
    signals_by_sid = batch_message_signals(db, session_ids)

    # latest message for snippet (all senders)
    latest_msg_sq = (
        db.query(
            ChatMessage.id.label("id"),
            ChatMessage.session_id.label("session_id"),
            func.row_number()
            .over(
                partition_by=ChatMessage.session_id,
                order_by=(ChatMessage.created_at.desc(), ChatMessage.id.desc()),
            )
            .label("rn"),
        )
        .filter(ChatMessage.session_id.in_(session_ids))
    ).subquery()

    latest_msgs = (
        db.query(ChatMessage)
        .join(latest_msg_sq, ChatMessage.id == latest_msg_sq.c.id)
        .filter(latest_msg_sq.c.rn == 1)
        .all()
    )
    last_msg_map = {int(m.session_id): m for m in latest_msgs}

    # --------------------------------------------------
    # Build rows
    # --------------------------------------------------
    items = []
    q_lower = (q or "").strip().lower()
    auto_escalated = 0

    for sess, prop in rows:
        sid = int(sess.id)

        sdata = signals_by_sid.get(sid, {})
        cnt24 = int(sdata.get("cnt24", 0) or 0)
        cnt7 = int(sdata.get("cnt7", 0) or 0)
        has_urgent = bool(sdata.get("has_urgent", False))
        has_negative = bool(sdata.get("has_negative", False))
        last_guest_text = sdata.get("last_guest_text")

        # legacy priority filters
        if priority == "urgent" and not has_urgent:
            continue
        if priority == "unhappy" and not has_negative:
            continue

        # latest message snippet
        last_msg = last_msg_map.get(sid)
        last_text = (last_msg.content or "") if last_msg else ""
        snippet = (last_text[:120] + "…") if len(last_text) > 120 else last_text

        # search filter
        if q_lower:
            hay = f"{prop.property_name} {sess.guest_name or ''} {snippet}".lower()
            if q_lower not in hay:
                continue

        status_val = (sess.reservation_status or "pre_booking").strip().lower() or "pre_booking"

        emotional_signals = derive_guest_mood(
            has_urgent=has_urgent,
            has_negative=has_negative,
            cnt24=cnt24,
            cnt7=cnt7,
            status_val=status_val,
            last_guest_text=last_guest_text,
        )
        guest_mood_val = emotional_signals[0] if emotional_signals else None

        # mood filter
        if mood and mood not in {s.lower() for s in (emotional_signals or [])}:
            continue

        raw_heat = (
            (50 if has_urgent else 0)
            + (25 if has_negative else 0)
            + min(25, cnt24 * 5)
            + min(10, cnt7)
        )
        raw_heat = min(100, raw_heat)

        boosted = raw_heat
        if has_urgent:
            boosted = int(boosted * 1.3)
        if has_negative:
            boosted = int(boosted * 1.15)
        if status_val == "active":
            boosted = int(boosted * 1.1)

        heat = decay_heat(min(100, boosted), sess.last_activity_at)

        action_priority_val = compute_action_priority(
            heat=heat,
            signals=emotional_signals,
            has_urgent=has_urgent,
            has_negative=has_negative,
        )

        # auto escalation
        desired = desired_escalation_level(heat)
        current = (sess.escalation_level or "").lower() or None

        if not sess.is_resolved and escalation_rank(desired) > escalation_rank(current):
            sess.escalation_level = desired
            sess.updated_at = datetime.utcnow()
            db.add(sess)
            auto_escalated += 1

        items.append(
            {
                "id": sess.id,
                "property_id": sess.property_id,
                "property_name": prop.property_name or "Unknown",
                "guest_name": sess.guest_name,
                "reservation_status": status_val,
                "last_activity_at": sess.last_activity_at,
                "last_snippet": snippet,

                "emotional_signals": emotional_signals,
                "guest_mood": guest_mood_val,

                "has_urgent": has_urgent,
                "has_negative": has_negative,

                "msg_24h": cnt24,
                "msg_7d": cnt7,

                "heat": heat,
                "heat_raw": raw_heat,

                "priority_level": action_priority_val,
                "action_priority": action_priority_val,

                "assigned_to": sess.assigned_to,
                "escalation_level": sess.escalation_level,
                "is_resolved": bool(sess.is_resolved),
                "needs_attention": (sess.escalation_level == "high" and not sess.is_resolved),
            }
        )

    if auto_escalated:
        db.commit()

    items.sort(
        key=lambda x: (x["heat"], x["last_activity_at"] or datetime.min),
        reverse=True,
    )

    return templates.TemplateResponse(
        "admin_chats.html",
        {
            "request": request,
            "sessions": items,
            "filters": {
                "status": status,
                "priority": priority,
                "q": q,
                "pmc_id": (str(pmc_obj.id) if user_role == "pmc" else (pmc_id or "")),
                "property_id": property_id or "",
                "mine": bool(mine),
                "assigned_to": effective_assignee,
                "guest_mood": mood,
            },
            "guest_mood_choices": GUEST_MOOD_CHOICES,
            "pmcs": pmcs,
            "properties": properties,
            "user_role": user_role,
        },
    )



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

class AssignPayload(BaseModel):
    assigned_to: str | None = None


def require_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        # Use 401 for "not logged in" (NOT 403)
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def get_chat_session_in_scope(db: Session, session_id: int, user_role: str, pmc_obj):
    """
    Returns ChatSession if user can access it; otherwise raises 404 (or 403 if you prefer).
    """
    q = (
        db.query(ChatSession)
        .join(Property, Property.id == ChatSession.property_id)
        .filter(ChatSession.id == session_id)
    )

    if user_role == "pmc":
        if not pmc_obj:
            raise HTTPException(status_code=403, detail="PMC scope not found")
        q = q.filter(Property.pmc_id == pmc_obj.id)

    obj = q.first()
    if not obj:
        # 404 avoids leaking existence of sessions outside scope
        raise HTTPException(status_code=404, detail="Chat not found")
    return obj
    
@router.post("/admin/chats/{session_id}/assign")
def assign_chat(
    session_id: int,
    payload: AssignPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    # must be logged in
    require_user(request)

    # resolve role + scope (your existing helper)
    user_role, pmc_obj, pmc_user, billing_status, needs_payment = get_user_role_and_scope(request, db)

    # load session in scope
    sess = get_chat_session_in_scope(db, session_id, user_role, pmc_obj)

    # apply assignment
    assigned_to = (payload.assigned_to or "").strip()
    sess.assigned_to = assigned_to or None

    db.add(sess)
    db.commit()

    return {"ok": True, "assigned_to": sess.assigned_to}


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
async def summarize_chat(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    # Keep your existing access control
    session = require_session_in_scope(request, db, session_id)

    # Manual click should always refresh (ignore throttling)
    did_run, summary, err = generate_and_store_summary(
        db=db,
        session_id=int(session.id),
        force=True,
    )

    if err:
        logging.exception("Summarization failed")
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": "Summarization failed",
                "detail": err,
                "model": os.getenv("OPENAI_SUMMARY_MODEL", "gpt-4o-mini"),
            },
        )

    # refresh so timestamps are current in the response
    db.refresh(session)

    return {
        "ok": True,
        "summary": summary,
        "updated_at": (session.ai_summary_updated_at.isoformat() if session.ai_summary_updated_at else None),
        "did_run": bool(did_run),
    }



# ✅ FIXED: scoped analytics by role
@router.get("/admin/analytics/chats")
def chats_analytics(request: Request, db: Session = Depends(get_db)):
    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)
    require_pmc_linked(user_role, pmc_obj)

    base_sessions = db.query(ChatSession)
    base_urgent = db.query(ChatSession.id).join(ChatMessage)
    base_unhappy = db.query(ChatSession.id).join(ChatMessage)

    if user_role == "pmc":
        base_sessions = base_sessions.join(Property, ChatSession.property_id == Property.id).filter(Property.pmc_id == pmc_obj.id)
        base_urgent = base_urgent.join(Property, ChatSession.property_id == Property.id).filter(Property.pmc_id == pmc_obj.id)
        base_unhappy = base_unhappy.join(Property, ChatSession.property_id == Property.id).filter(Property.pmc_id == pmc_obj.id)

    by_status = dict(
        base_sessions.with_entities(ChatSession.reservation_status, func.count(ChatSession.id))
        .group_by(ChatSession.reservation_status)
        .all()
    )

    urgent_sessions = (
        base_urgent
        .filter(ChatMessage.sender == "guest", ChatMessage.category == "urgent")
        .with_entities(func.count(func.distinct(ChatSession.id)))
        .scalar()
        or 0
    )
    
    unhappy_sessions = (
        base_unhappy
        .filter(ChatMessage.sender == "guest", ChatMessage.sentiment == "negative")
        .with_entities(func.count(func.distinct(ChatSession.id)))
        .scalar()
        or 0
    )


    total_sessions = base_sessions.with_entities(func.count(ChatSession.id)).scalar() or 0

    return {
        "total_sessions": int(total_sessions),
        "by_status": {
            "pre_booking": int(by_status.get("pre_booking", 0)),
            "active": int(by_status.get("active", 0)),
            "post_stay": int(by_status.get("post_stay", 0)),
        },
        "sessions_flagged": {
            "urgent": int(urgent_sessions),
            "unhappy": int(unhappy_sessions),
        },
    }

# ----------------------------
# Analytics: Summary + Top Properties (scoped)
# ----------------------------

def _apply_scope_to_property_query(q, user_role: str, pmc_obj: Optional[PMC]):
    """
    Ensures PMC users only see their PMC's properties.
    Super users see all.
    """
    if user_role == "pmc":
        require_pmc_linked(user_role, pmc_obj)
        q = q.filter(Property.pmc_id == pmc_obj.id)
    return q


def _apply_scope_to_session_query(q, user_role: str, pmc_obj: Optional[PMC]):
    """
    Ensures PMC users only see sessions under their PMC's properties.
    Super users see all.
    Expects q joined to Property OR joins it here.
    """
    # If Property isn't already joined, join it
    try:
        # This is safe even if already joined in most SA setups, but if you prefer:
        # you can remove and require caller to join.
        q = q.join(Property, Property.id == ChatSession.property_id)
    except Exception:
        pass

    if user_role == "pmc":
        require_pmc_linked(user_role, pmc_obj)
        q = q.filter(Property.pmc_id == pmc_obj.id)
    return q


# -------------------------------------------------------------------
# Aliases to match admin dashboard frontend endpoint expectations
# (keeps your existing routes working too)
# -------------------------------------------------------------------

@router.get("/admin/analytics/chat/summary")
def admin_analytics_chat_summary(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=365),
):
    return admin_analytics_summary(request=request, db=db, days=days)



@router.get("/admin/analytics/chat/top-properties")
def admin_analytics_chat_top_properties(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(10, ge=1, le=50),
    sort: str = Query("messages", pattern="^(messages|sessions|urgent_sessions|unhappy_sessions)$"),
):
    return admin_analytics_top_properties(request=request, db=db, days=days, limit=limit, sort=sort)


@router.get("/admin/analytics/chat/timeseries")
def admin_analytics_chat_timeseries(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=365),
):
    # Safe placeholder so the UI doesn't crash if it expects this endpoint.
    # Replace later with real daily counts.
    return {
        "window_days": int(days),
        "series": [],   # e.g. [{"date":"2026-01-01","messages":12,"sessions":3}, ...]
    }


@router.get("/admin/analytics/summary")
def admin_analytics_summary(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=365),
):
    """
    High-level summary cards for dashboard:
    - properties total / enabled
    - sessions total / last N days
    - messages last N days
    - urgent/unhappy sessions last N days
    Scoped to PMC if user_role == "pmc"
    """
    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)
    require_pmc_linked(user_role, pmc_obj)

    cutoff = datetime.utcnow() - timedelta(days=int(days))

    # Properties
    props_q = db.query(Property)
    props_q = _apply_scope_to_property_query(props_q, user_role, pmc_obj)

    total_properties = props_q.with_entities(func.count(Property.id)).scalar() or 0
    enabled_properties = props_q.with_entities(
        func.count(case((Property.chat_enabled.is_(True), 1)))
    ).scalar() or 0

    # Sessions
    sess_q = db.query(ChatSession)
    sess_q = _apply_scope_to_session_query(sess_q, user_role, pmc_obj)

    total_sessions = sess_q.with_entities(func.count(ChatSession.id)).scalar() or 0
    sessions_last_n_days = sess_q.filter(ChatSession.last_activity_at >= cutoff).with_entities(
        func.count(ChatSession.id)
    ).scalar() or 0

    # Messages last N days
    msg_q = (
        db.query(func.count(ChatMessage.id))
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .join(Property, Property.id == ChatSession.property_id)
        .filter(ChatMessage.created_at >= cutoff)
    )
    if user_role == "pmc":
        msg_q = msg_q.filter(Property.pmc_id == pmc_obj.id)

    messages_last_n_days = msg_q.scalar() or 0

    # Urgent/unhappy sessions last N days (distinct sessions)
    urgent_sessions_q = (
        db.query(func.count(func.distinct(ChatMessage.session_id)))
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .join(Property, Property.id == ChatSession.property_id)
        .filter(ChatMessage.created_at >= cutoff)
        .filter(ChatMessage.sender == "guest")
        .filter(ChatMessage.category == "urgent")
    )
    unhappy_sessions_q = (
        db.query(func.count(func.distinct(ChatMessage.session_id)))
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .join(Property, Property.id == ChatSession.property_id)
        .filter(ChatMessage.created_at >= cutoff)
        .filter(ChatMessage.sender == "guest")
        .filter(func.lower(func.coalesce(ChatMessage.sentiment, "")) == "negative")
    )

    if user_role == "pmc":
        urgent_sessions_q = urgent_sessions_q.filter(Property.pmc_id == pmc_obj.id)
        unhappy_sessions_q = unhappy_sessions_q.filter(Property.pmc_id == pmc_obj.id)

    urgent_sessions = urgent_sessions_q.scalar() or 0
    unhappy_sessions = unhappy_sessions_q.scalar() or 0

    return {
        "window_days": int(days),
        "properties": {
            "total": int(total_properties),
            "chat_enabled": int(enabled_properties),
        },
        "sessions": {
            "total": int(total_sessions),
            "active_last_n_days": int(sessions_last_n_days),
        },
        "messages": {
            "last_n_days": int(messages_last_n_days),
        },
        "flags": {
            "urgent_sessions_last_n_days": int(urgent_sessions),
            "unhappy_sessions_last_n_days": int(unhappy_sessions),
        },
    }


@router.get("/admin/analytics/top-properties")
def admin_analytics_top_properties(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(10, ge=1, le=50),
    sort: str = Query("messages", pattern="^(messages|sessions|urgent_sessions|unhappy_sessions)$"),
):
    """
    Top properties leaderboard over last N days.
    sort:
      - messages
      - sessions
      - urgent_sessions
      - unhappy_sessions
    Scoped to PMC if user_role == "pmc"
    """
    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)
    require_pmc_linked(user_role, pmc_obj)

    cutoff = datetime.utcnow() - timedelta(days=int(days))

    # Base: properties in scope
    prop_q = db.query(Property.id, Property.property_name)
    prop_q = _apply_scope_to_property_query(prop_q, user_role, pmc_obj)
    prop_rows = prop_q.all()
    prop_ids = [int(p.id) for p in prop_rows]
    if not prop_ids:
        return {"window_days": int(days), "items": []}

    # Sessions per property (last N days by last_activity_at)
    sessions_counts = dict(
        db.query(ChatSession.property_id, func.count(ChatSession.id))
        .filter(ChatSession.property_id.in_(prop_ids))
        .filter(ChatSession.last_activity_at >= cutoff)
        .group_by(ChatSession.property_id)
        .all()
    )

    # Messages per property (last N days)
    messages_counts = dict(
        db.query(ChatSession.property_id, func.count(ChatMessage.id))
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .filter(ChatSession.property_id.in_(prop_ids))
        .filter(ChatMessage.created_at >= cutoff)
        .group_by(ChatSession.property_id)
        .all()
    )

    # Urgent sessions per property (distinct sessions, last N days)
    urgent_sessions_counts = dict(
        db.query(ChatSession.property_id, func.count(func.distinct(ChatMessage.session_id)))
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .filter(ChatSession.property_id.in_(prop_ids))
        .filter(ChatMessage.created_at >= cutoff)
        .filter(ChatMessage.sender == "guest")
        .filter(ChatMessage.category == "urgent")
        .group_by(ChatSession.property_id)
        .all()
    )

    # Unhappy sessions per property (distinct sessions, last N days)
    unhappy_sessions_counts = dict(
        db.query(ChatSession.property_id, func.count(func.distinct(ChatMessage.session_id)))
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .filter(ChatSession.property_id.in_(prop_ids))
        .filter(ChatMessage.created_at >= cutoff)
        .filter(ChatMessage.sender == "guest")
        .filter(func.lower(func.coalesce(ChatMessage.sentiment, "")) == "negative")
        .group_by(ChatSession.property_id)
        .all()
    )

    # Build leaderboard
    items = []
    name_by_id = {int(p.id): (p.property_name or "") for p in prop_rows}

    for pid in prop_ids:
        items.append({
            "property_id": int(pid),
            "property_name": name_by_id.get(int(pid), "") or "Unknown",
            "sessions": int(sessions_counts.get(pid, 0) or 0),
            "messages": int(messages_counts.get(pid, 0) or 0),
            "urgent_sessions": int(urgent_sessions_counts.get(pid, 0) or 0),
            "unhappy_sessions": int(unhappy_sessions_counts.get(pid, 0) or 0),
        })

    items.sort(key=lambda x: (x.get(sort, 0), x["messages"], x["sessions"]), reverse=True)

    return {
        "window_days": int(days),
        "sort": sort,
        "limit": int(limit),
        "items": items[: int(limit)],
    }


@router.get("/admin/guides/partial/list", response_class=HTMLResponse)
def guides_partial_list(
    request: Request,
    db: Session = Depends(get_db),
    property_id: Optional[int] = Query(None),
):
    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)

    q = (
        db.query(Guide, Property)
        .join(Property, Guide.property_id == Property.id)
    )

    if user_role == "pmc":
        require_pmc_linked(user_role, pmc_obj)
        q = q.filter(Property.pmc_id == pmc_obj.id)

    if property_id is not None:
        q = q.filter(Guide.property_id == int(property_id))

    guides = q.order_by(Guide.sort_order.asc(), Guide.updated_at.desc()).all()

    rows = [
        {
            "id": g.id,
            "title": g.title,
            "property_id": g.property_id,
            "property_name": p.property_name,
            "is_active": g.is_active,
            "updated_at": g.updated_at,
        }
        for (g, p) in guides
    ]

    return templates.TemplateResponse(
        "admin/_guides_list.html",
        {"request": request, "guides": rows},
    )


@router.get("/admin/guides/partial/form", response_class=HTMLResponse)
def guides_partial_form(
    request: Request,
    db: Session = Depends(get_db),
    id: Optional[int] = Query(None),
):
    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)

    # property dropdown options (scope-aware)
    props_q = db.query(Property)
    if user_role == "pmc":
        require_pmc_linked(user_role, pmc_obj)
        props_q = props_q.filter(Property.pmc_id == pmc_obj.id)
    properties = props_q.order_by(Property.property_name.asc()).all()

    guide = None
    if id:
        guide = db.query(Guide).filter(Guide.id == int(id)).first()
        if not guide:
            raise HTTPException(status_code=404, detail="Not found")
        # scope check
        require_property_in_scope(request, db, int(guide.property_id))

    return templates.TemplateResponse(
        "admin/_guides_form.html",
        {"request": request, "guide": guide, "properties": properties},
    )


@router.post("/admin/guides/ajax/save")
def guides_ajax_save(
    request: Request,
    db: Session = Depends(get_db),
    id: Optional[int] = Form(None),
    property_id: int = Form(...),
    title: str = Form(...),
    category: Optional[str] = Form(None),
    body_html: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
):
    require_property_in_scope(request, db, int(property_id))

    if id:
        g = db.query(Guide).filter(Guide.id == int(id)).first()
        if not g:
            return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
        require_property_in_scope(request, db, int(g.property_id))
    else:
        g = Guide(property_id=int(property_id))

    g.property_id = int(property_id)
    g.title = title.strip()
    g.category = (category or "").strip() or None
    g.body_html = body_html or None
    g.is_active = bool(is_active)  # checkbox => "true" present or None
    g.updated_at = datetime.utcnow()

    db.add(g)
    db.commit()
    return {"ok": True, "id": g.id}


@router.post("/admin/guides/ajax/delete")
def guides_ajax_delete(request: Request, db: Session = Depends(get_db), id: int = Query(...)):
    g = db.query(Guide).filter(Guide.id == int(id)).first()
    if not g:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    require_property_in_scope(request, db, int(g.property_id))
    db.delete(g)
    db.commit()
    return {"ok": True}



def _is_image(file: UploadFile) -> bool:
    return (file.content_type or "").lower().startswith(ALLOWED_MIME_PREFIX)


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists() and path.is_file():
            path.unlink()
    except Exception:
        pass

@router.post("/admin/upgrades/ajax/upload-image")
async def upload_upgrade_image(
    file: UploadFile = File(...),
    prev_tmp_key: str = Form(default=""),
):
    if not file:
        return JSONResponse({"ok": False, "error": "No file provided."}, status_code=400)

    if not _is_image(file):
        return JSONResponse({"ok": False, "error": "Please upload an image file."}, status_code=400)

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        return JSONResponse({"ok": False, "error": "Unsupported image type."}, status_code=400)

    # delete previous temp if provided
    if prev_tmp_key:
        delete_temp_upgrade_image(prev_tmp_key)

    tmp_key = f"{uuid.uuid4().hex}{ext}"
    tmp_path = TMP_DIR / tmp_key

    size = 0
    try:
        with tmp_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    out.close()
                    _safe_unlink(tmp_path)
                    return JSONResponse({"ok": False, "error": "File too large (max 8MB)."}, status_code=400)
                out.write(chunk)
    finally:
        try:
            await file.close()
        except Exception:
            pass

    return {
        "ok": True,
        "tmp_key": tmp_key,
        "preview_url": f"{TMP_URL_PREFIX}/{tmp_key}",
    }

@router.get("/admin/upgrades/partial/list", response_class=HTMLResponse)
def upgrades_partial_list(
    request: Request,
    db: Session = Depends(get_db),
    property_id: Optional[int] = Query(None),
):
    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)

    q = (
        db.query(Upgrade, Property)
        .join(Property, Upgrade.property_id == Property.id)
    )

    if user_role == "pmc":
        require_pmc_linked(user_role, pmc_obj)
        q = q.filter(Property.pmc_id == pmc_obj.id)

    if property_id is not None:
        q = q.filter(Upgrade.property_id == int(property_id))

    upgrades = (
        q.order_by(Upgrade.sort_order.asc(), Upgrade.updated_at.desc())
        .all()
    )

    rows = [
        {
            "id": u.id,
            "title": u.title,
            "slug": u.slug,
            "property_id": u.property_id,
            "property_name": p.property_name,  # ✅ this is what your table should display
            "price_cents": u.price_cents,
            "is_active": u.is_active,
            "image_url": getattr(u, "image_url", None),
        }
        for (u, p) in upgrades
    ]

    return templates.TemplateResponse(
        "admin/_upgrades_list.html",
        {"request": request, "upgrades": rows},
    )



@router.post("/admin/guides/ajax/toggle-active")
def guides_ajax_toggle_active(
    request: Request,
    db: Session = Depends(get_db),
    payload: dict = Body(...),
):
    guide_id = int(payload.get("id") or 0)
    is_active = bool(payload.get("is_active"))

    g = db.query(Guide).filter(Guide.id == guide_id).first()
    if not g:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    require_property_in_scope(request, db, int(g.property_id))

    g.is_active = is_active
    g.updated_at = datetime.utcnow()
    db.add(g)
    db.commit()
    return {"ok": True, "id": g.id, "is_active": g.is_active}


@router.post("/admin/upgrades/ajax/toggle-active")
def upgrades_ajax_toggle_active(
    request: Request,
    db: Session = Depends(get_db),
    payload: dict = Body(...),
):
    upgrade_id = int(payload.get("id") or 0)
    is_active = bool(payload.get("is_active"))

    u = db.query(Upgrade).filter(Upgrade.id == upgrade_id).first()
    if not u:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    require_property_in_scope(request, db, int(u.property_id))

    u.is_active = is_active
    u.updated_at = datetime.utcnow()
    db.add(u)
    db.commit()
    return {"ok": True, "id": u.id, "is_active": u.is_active}


@router.get("/admin/upgrades/partial/form", response_class=HTMLResponse)
def upgrades_partial_form(
    request: Request,
    db: Session = Depends(get_db),
    id: Optional[int] = Query(None),
):
    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)

    props_q = db.query(Property)
    if user_role == "pmc":
        require_pmc_linked(user_role, pmc_obj)
        props_q = props_q.filter(Property.pmc_id == pmc_obj.id)
    properties = props_q.order_by(Property.property_name.asc()).all()

    upgrade = None
    if id:
        upgrade = db.query(Upgrade).filter(Upgrade.id == int(id)).first()
        if not upgrade:
            raise HTTPException(status_code=404, detail="Not found")
        require_property_in_scope(request, db, int(upgrade.property_id))

    return templates.TemplateResponse(
        "admin/_upgrades_form.html",
        {"request": request, "upgrade": upgrade, "properties": properties},
    )

def dollars_to_cents(s: str) -> int:
    try:
        s = (s or "0").strip().replace("$", "")
        return int(round(float(s) * 100))
    except Exception:
        return 0




@router.post("/admin/upgrades/ajax/save")
async def save_upgrade(request: Request, db: Session = Depends(get_db)):
    form = await request.form()

    # ----------------------------
    # Parse fields
    # ----------------------------
    upgrade_id_raw = (form.get("id") or "").strip()
    upgrade_id = int(upgrade_id_raw) if upgrade_id_raw.isdigit() else None

    title = (form.get("title") or "").strip()
    slug = (form.get("slug") or "").strip()
    property_id_raw = (form.get("property_id") or "").strip()
    long_description = (form.get("long_description") or "").strip() or None

    # checkbox: present => True
    is_active = form.get("is_active") is not None

    # price dollars -> cents
    price_raw = (form.get("price_dollars") or "0").strip().replace("$", "")
    try:
        price_cents = int(round(float(price_raw) * 100))
        price_cents = max(0, price_cents)
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid price."}, status_code=400)

    if not title or not slug:
        return JSONResponse({"ok": False, "error": "Title and slug are required."}, status_code=400)

    if not property_id_raw.isdigit():
        return JSONResponse({"ok": False, "error": "Property is required."}, status_code=400)

    property_id = int(property_id_raw)

    # ----------------------------
    # Image handling
    # ----------------------------
    # Persisted image_url hidden field (blank means "remove")
    current_image_url = (form.get("image_url") or "").strip() or None

    # Temp key returned by upload endpoint (means "new image pending")
    tmp_key = (form.get("image_tmp_key") or "").strip() or None

    # Default to whatever the form currently says (including None for remove)
    new_image_url = current_image_url

    # If a tmp_key is provided, promote tmp -> permanent and override image_url
    if tmp_key:
        safe_tmp_key = Path(tmp_key).name  # prevents "../"
        tmp_path = TMP_DIR / safe_tmp_key

        if not (tmp_path.exists() and tmp_path.is_file()):
            return JSONResponse(
                {"ok": False, "error": "Uploaded image not found. Please upload again."},
                status_code=400,
            )

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest_path = UPLOAD_DIR / safe_tmp_key

        # shutil.move handles rename/cross-device cases
        shutil.move(str(tmp_path), str(dest_path))

        new_image_url = f"{FINAL_URL_PREFIX}/{safe_tmp_key}"

    # ----------------------------
    # Fetch/create + scope checks
    # ----------------------------
    old_image_url = None

    if upgrade_id:
        upgrade = db.query(Upgrade).filter(Upgrade.id == upgrade_id).first()
        if not upgrade:
            return JSONResponse({"ok": False, "error": "Upgrade not found."}, status_code=404)

        # Must be allowed to edit the existing record
        require_property_in_scope(request, db, int(upgrade.property_id))

        # Also must be allowed to move it to a different property (if changed in UI)
        require_property_in_scope(request, db, int(property_id))

        old_image_url = (getattr(upgrade, "image_url", None) or "").strip() or None

        upgrade.title = title
        upgrade.slug = slug
        upgrade.property_id = property_id
        upgrade.long_description = long_description
        upgrade.price_cents = price_cents
        upgrade.is_active = is_active
        upgrade.image_url = new_image_url

        if hasattr(upgrade, "updated_at"):
            upgrade.updated_at = datetime.utcnow()

    else:
        # Creating: must be allowed in target property scope
        require_property_in_scope(request, db, int(property_id))

        upgrade = Upgrade(
            title=title,
            slug=slug,
            property_id=property_id,
            long_description=long_description,
            price_cents=price_cents,
            is_active=is_active,
            image_url=new_image_url,
        )
        if hasattr(upgrade, "updated_at"):
            upgrade.updated_at = datetime.utcnow()

        db.add(upgrade)

    # ----------------------------
    # Commit
    # ----------------------------
    try:
        db.commit()
        db.refresh(upgrade)
    except Exception:
        db.rollback()
        return JSONResponse({"ok": False, "error": "Save failed."}, status_code=500)

    # ----------------------------
    # Cleanup old image after commit
    # ----------------------------
    if old_image_url and old_image_url != new_image_url:
        delete_local_upgrade_image(old_image_url)

    return {"ok": True, "id": int(upgrade.id)}

'''
@router.post("/admin/upgrades/ajax/toggle-active")
def upgrades_toggle_active(
    request: Request,
    db: Session = Depends(get_db),
    id: int = Form(...),
    is_active: str = Form("false"),
):
    u = db.query(Upgrade).filter(Upgrade.id == int(id)).first()
    if not u:
        raise HTTPException(status_code=404, detail="Not found")
    require_property_in_scope(request, db, int(u.property_id))

    u.is_active = (is_active.lower() in ("true", "1", "on", "yes"))
    u.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "is_active": u.is_active}

'''
@router.post("/admin/upgrades/ajax/delete")
def upgrades_ajax_delete(request: Request, db: Session = Depends(get_db), id: int = Query(...)):
    u = db.query(Upgrade).filter(Upgrade.id == int(id)).first()
    if not u:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    require_property_in_scope(request, db, int(u.property_id))
    db.delete(u)
    db.commit()
    return {"ok": True}


@router.post("/admin/upgrades/ajax/reorder")
def upgrades_ajax_reorder(request: Request, db: Session = Depends(get_db), payload: dict = Body(...)):
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return JSONResponse({"ok": False, "error": "Invalid ids"}, status_code=400)

    first = db.query(Upgrade).filter(Upgrade.id == int(ids[0])).first()
    if not first:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    require_property_in_scope(request, db, int(first.property_id))
    prop_id = int(first.property_id)

    for uid in ids:
        u = db.query(Upgrade).filter(Upgrade.id == int(uid)).first()
        if not u or int(u.property_id) != prop_id:
            return JSONResponse({"ok": False, "error": "All items must be from the same property"}, status_code=400)

    for idx, uid in enumerate(ids):
        db.query(Upgrade).filter(Upgrade.id == int(uid)).update({"sort_order": idx, "updated_at": datetime.utcnow()})

    db.commit()
    return {"ok": True}


@router.post("/admin/guides/ajax/reorder")
def guides_ajax_reorder(request: Request, db: Session = Depends(get_db), payload: dict = Body(...)):
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return JSONResponse({"ok": False, "error": "Invalid ids"}, status_code=400)

    first = db.query(Guide).filter(Guide.id == int(ids[0])).first()
    if not first:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    require_property_in_scope(request, db, int(first.property_id))
    prop_id = int(first.property_id)

    # enforce same property
    for gid in ids:
        g = db.query(Guide).filter(Guide.id == int(gid)).first()
        if not g or int(g.property_id) != prop_id:
            return JSONResponse({"ok": False, "error": "All items must be from the same property"}, status_code=400)

    for idx, gid in enumerate(ids):
        db.query(Guide).filter(Guide.id == int(gid)).update({"sort_order": idx, "updated_at": datetime.utcnow()})

    db.commit()
    return {"ok": True}

def require_property_in_scope(request: Request, db: Session, property_id: int) -> Property:
    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)

    prop = db.query(Property).filter(Property.id == int(property_id)).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    if user_role == "pmc":
        require_pmc_linked(user_role, pmc_obj)
        if prop.pmc_id != pmc_obj.id:
            raise HTTPException(status_code=403, detail="Forbidden property")

    return prop

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
    user_role, *_ = get_user_role_and_scope(request, db)

    return templates.TemplateResponse(
        "admin_chat_detail.html",
        {"request": request, "session": session, "property": prop, "messages": messages, "user_role": user_role},
    )


def _parse_optional_int(v) -> int | None:
    """
    Accepts: None, "", "  ", "123", 123
    Returns: int or None (never raises)
    """
    if v is None:
        return None
    if isinstance(v, int):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        return int(s)
    except Exception:
        return None



@router.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),

    # view routing + selection
    view: str | None = Query(default=None),
    session_id: int | None = Query(default=None),

    # filters (HTML form submits empty strings)
    pmc_id: str | None = Query(default=None),
    property_id: str | None = Query(default=None),
    status: str | None = Query(default=None),

    # ✅ CANONICAL (matches admin_dashboard.html)
    action_priority: str | None = Query(default=None),          # urgent|high|normal|low
    emotional_signals_filter: str | None = Query(default=None), # guest mood dropdown

    mine: int | None = Query(default=None),
    assigned_to: str | None = Query(default=None),
    q: str | None = Query(default=None),

    # ✅ BACKWARD COMPAT (keep old links working)
    priority: str | None = Query(default=None),       # legacy: "urgent" | "unhappy"
    guest_mood: str | None = Query(default=None),     # legacy alias for mood
    signals_filter: str | None = Query(default=None), # legacy alias for mood
):
    # ----------------------------
    # Helpers
    # ----------------------------
    def _clean_str(x: str | None) -> str | None:
        if x is None:
            return None
        s = str(x).strip()
        return s if s else None

    # ----------------------------
    # Parse + normalize inputs
    # ----------------------------
    pmc_id_int = _parse_optional_int(pmc_id)
    prop_id_int = _parse_optional_int(property_id)

    raw_mood = _clean_str(emotional_signals_filter) or _clean_str(guest_mood) or _clean_str(signals_filter)
    mood = normalize_guest_mood(raw_mood)

    ap_filter = _clean_str(action_priority)
    legacy_priority = _clean_str(priority)  # "urgent" | "unhappy" (legacy links)

    # ----------------------------
    # Auth
    # ----------------------------
    user = request.session.get("user")
    if not user:
        request.session["post_login_redirect"] = "/admin/dashboard"
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "next": "/admin/dashboard"},
        )

    user_role, pmc_obj, pmc_user, billing_status, needs_payment = get_user_role_and_scope(request, db)

    if user_role == "pmc" and not pmc_obj:
        return templates.TemplateResponse(
            "admin_login.html",
            {
                "request": request,
                "next": "/admin/dashboard",
                "error": "Your Google account isn’t linked to a PMC.",
            },
        )

    # ----------------------------
    # Billing gating
    # ----------------------------
    is_paid = True
    billing_banner_title = None
    billing_banner_body = None

    if user_role == "pmc" and pmc_obj:
        billing_status = (billing_status or "pending").lower()
        is_paid = (billing_status == "active") and bool(getattr(pmc_obj, "active", False))
        needs_payment = not is_paid

        if needs_payment:
            billing_banner_title = "Complete payment to activate your account"
            billing_banner_body = (
                "Your account is currently pending. Once payment is confirmed, you’ll be able to "
                "connect your PMS, sync properties, and enable Sandy per property."
            )

    # ----------------------------
    # Properties list (respect scope)
    # ----------------------------
    if user_role == "super":
        properties = db.query(Property).order_by(Property.property_name.asc()).all()
        allowed_property_ids: list[int] | None = None
    else:
        properties = (
            db.query(Property)
            .filter(Property.pmc_id == pmc_obj.id)
            .order_by(Property.property_name.asc())
            .all()
        )
        allowed_property_ids = [p.id for p in properties]

    property_name_by_id = {p.id: (p.property_name or "") for p in (properties or [])}

    # ----------------------------
    # Superuser-only PMC list
    # ----------------------------
    pmc_list = []
    if user_role == "super":
        pmc_list = db.query(PMC).order_by(PMC.pmc_name.asc()).all()

    # ----------------------------
    # Team + prefs
    # ----------------------------
    me_email = get_current_admin_identity(request)
    me_user = None
    team_members = []
    notif_prefs = {}

    if me_email:
        me_user = (
            db.query(PMCUser)
            .filter(func.lower(PMCUser.email) == me_email.lower())
            .first()
        )
        if me_user and getattr(me_user, "notification_prefs", None):
            notif_prefs = me_user.notification_prefs or {}

    if user_role == "pmc" and pmc_obj:
        team_members = (
            db.query(PMCUser)
            .filter(PMCUser.pmc_id == pmc_obj.id)
            .order_by(PMCUser.created_at.desc())
            .all()
        )

    # ----------------------------
    # Filters (echo back to template)
    # ----------------------------
    filters = {
        "pmc_id": pmc_id or "",
        "property_id": property_id or "",
        "status": status or "",
        "action_priority": ap_filter or "",
        "guest_mood": mood or "",
        "mine": bool(mine),
        "assigned_to": assigned_to or "",
        "q": q or "",
    }

    sessions: list[dict] = []
    analytics = {
        "pre_booking": 0,
        "post_booking": 0,
        "active": 0,
        "post_stay": 0,
        "urgent_sessions": 0,
        "unhappy_sessions": 0,
    }

    selected_session = None
    selected_property = None
    selected_messages: list[ChatMessage] = []

    # ✅ Always preload chats
    should_load_chats = True

    if should_load_chats:
        # Superusers can filter by pmc_id; PMCs cannot
        if user_role != "super":
            pmc_id_int = None

        # Effective assignee
        effective_assignee = None
        if mine and me_email:
            effective_assignee = me_email
        elif assigned_to:
            effective_assignee = assigned_to

        # ----------------------------
        # Fetch rows (NO mood filtering in SQL; mood is derived in Python)
        # NOTE: update fetch_dashboard_chat_sessions() to NOT accept emotional_signals_filter
        # ----------------------------
        rows = fetch_dashboard_chat_sessions(
            db,
            pmc_id=(pmc_id_int if user_role == "super" else None),
            property_id=prop_id_int,
            status=status,
            action_priority=ap_filter,
            q=q,
            limit=200,
        )

        # Legacy priority filter (urgent/unhappy) applied here as a secondary filter
        # (keeps old links working without complicating SQL)
        if legacy_priority in {"urgent", "unhappy"}:
            filtered = []
            for r in rows:
                heat_score = int(r.get("heat_score") or 0)
                if legacy_priority == "urgent" and heat_score < 80:
                    continue
                if legacy_priority == "unhappy" and heat_score < 50:
                    continue
                filtered.append(r)
            rows = filtered

        session_ids = [int(r["id"]) for r in rows]
        signals_by_sid = batch_message_signals(db, session_ids) if session_ids else {}

        # latest message overall for snippets (all senders)
        last_msg_by_session: dict[int, ChatMessage] = {}
        if session_ids:
            latest_msg_sq = (
                db.query(
                    ChatMessage.id.label("id"),
                    ChatMessage.session_id.label("session_id"),
                    func.row_number()
                    .over(
                        partition_by=ChatMessage.session_id,
                        order_by=(ChatMessage.created_at.desc(), ChatMessage.id.desc()),
                    )
                    .label("rn"),
                )
                .filter(ChatMessage.session_id.in_(session_ids))
            ).subquery()

            latest_msgs = (
                db.query(ChatMessage)
                .join(latest_msg_sq, ChatMessage.id == latest_msg_sq.c.id)
                .filter(latest_msg_sq.c.rn == 1)
                .all()
            )
            last_msg_by_session = {int(m.session_id): m for m in latest_msgs}

        # Load session ORM objects for persistence (optional)
        sess_map: dict[int, ChatSession] = {}
        if session_ids:
            sess_map = {
                int(s.id): s
                for s in db.query(ChatSession).filter(ChatSession.id.in_(session_ids)).all()
            }

        # ----------------------------
        # Build sessions list
        # ----------------------------
        dirty_any = False
        sessions = []

        for r in rows:
            sid = int(r["id"])
            status_val = (r.get("reservation_status") or "pre_booking").strip().lower() or "pre_booking"
            heat_score = int(r.get("heat_score") or 0)

            sdata = signals_by_sid.get(sid, {})
            has_urgent = bool(sdata.get("has_urgent", False))
            has_negative = bool(sdata.get("has_negative", False))
            cnt24 = int(sdata.get("cnt24", 0) or 0)
            cnt7 = int(sdata.get("cnt7", 0) or 0)
            last_guest_text = sdata.get("last_guest_text")

            emotional_signals = derive_guest_mood(
                has_urgent=has_urgent,
                has_negative=has_negative,
                cnt24=cnt24,
                cnt7=cnt7,
                status_val=status_val,
                last_guest_text=last_guest_text,
            )
            guest_mood_val = emotional_signals[0] if emotional_signals else None

            # ✅ mood filter uses DERIVED mood
            if mood and mood not in {s.lower() for s in (emotional_signals or [])}:
                continue

            action_priority_val = compute_action_priority(
                heat=heat_score,
                signals=emotional_signals,
                has_urgent=has_urgent,
                has_negative=has_negative,
            )

            # persist triage fields if columns exist
            sess_obj = sess_map.get(sid)
            if sess_obj:
                dirty_any |= persist_session_triage_fields(
                    db,
                    sess_obj,
                    emotional_signals=emotional_signals,
                    action_priority=action_priority_val,
                    guest_mood=guest_mood_val,
                )

            last_msg = last_msg_by_session.get(sid)
            last_sentiment = (getattr(last_msg, "sentiment", None) or "").strip().lower() if last_msg else ""

            last_snip = (r.get("last_message") or "")
            if not last_snip and last_msg and last_msg.content:
                last_snip = last_msg.content
            last_snip = (last_snip[:120] + "…") if last_snip and len(last_snip) > 120 else (last_snip or "")

            prop_id = int(r.get("property_id") or 0) or None

            sessions.append({
                "id": sid,
                "property_id": prop_id,
                "property_name": (
                    r.get("property_name")
                    or (property_name_by_id.get(prop_id) if prop_id else "")
                    or "Unknown property"
                ),
                "guest_name": r.get("guest_name"),
                "assigned_to": r.get("assigned_to"),
                "reservation_status": status_val,

                "source": r.get("source"),
                "last_activity_at": r.get("last_activity_at"),
                "last_snippet": last_snip,

                "action_priority": action_priority_val,
                "guest_mood": guest_mood_val,
                "emotional_signals": emotional_signals,

                "is_resolved": bool(r.get("is_resolved")),
                "escalation_level": r.get("escalation_level"),

                "has_urgent": has_urgent,
                "has_negative": has_negative,
                "last_sentiment": last_sentiment,
                "msg_24h": cnt24,
                "msg_7d": cnt7,

                "heat": heat_score,
                "heat_raw": heat_score,

                "pms_reservation_id": r.get("pms_reservation_id"),
                "arrival_date": r.get("arrival_date"),
                "departure_date": r.get("departure_date"),
            })

        if dirty_any:
            db.commit()

        # ----------------------------
        # Analytics from FINAL rendered sessions list
        # ----------------------------
        analytics = {
            "pre_booking": 0,
            "post_booking": 0,
            "active": 0,
            "post_stay": 0,
            "urgent_sessions": 0,
            "unhappy_sessions": 0,
        }

        for row in sessions:
            stage = _effective_stage_from_dict(row)
            if stage in ("pre_booking", "post_booking", "active", "post_stay"):
                analytics[stage] += 1
            if row.get("has_urgent"):
                analytics["urgent_sessions"] += 1
            if row.get("has_negative"):
                analytics["unhappy_sessions"] += 1

        # ----------------------------
        # Selected session detail (right panel)
        # ----------------------------
        if session_id is not None:
            sel_q = db.query(ChatSession).filter(ChatSession.id == session_id)

            # scope
            if allowed_property_ids is not None:
                sel_q = sel_q.filter(ChatSession.property_id.in_(allowed_property_ids))

            # super + pmc filter
            if user_role == "super" and pmc_id_int:
                sel_q = (
                    sel_q.join(Property, Property.id == ChatSession.property_id)
                    .filter(Property.pmc_id == pmc_id_int)
                )

            selected_session = sel_q.first()
            if not selected_session:
                raise HTTPException(status_code=404, detail="Chat not found")

            selected_property = db.query(Property).filter(Property.id == selected_session.property_id).first()
            selected_messages = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_id == selected_session.id)
                .order_by(ChatMessage.created_at.asc())
                .all()
            )

    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "user_role": user_role,
            "pmc_name": (pmc_obj.pmc_name if pmc_obj else "HostScout"),
            "properties": properties,
            "property_name_by_id": property_name_by_id,
            "now": datetime.utcnow(),

            "pmcs": pmc_list,

            "billing_status": billing_status,
            "is_paid": is_paid,
            "needs_payment": needs_payment,
            "billing_banner_title": billing_banner_title,
            "billing_banner_body": billing_banner_body,

            "user_timezone": (pmc_user.timezone if pmc_user else None),
            "pmc_user_role": (pmc_user.role if pmc_user else None),

            "user_email": me_email,
            "user_full_name": (me_user.full_name if me_user else None),
            "team_members": team_members,
            "notif_prefs": notif_prefs,

            "sessions": sessions,
            "analytics": analytics,
            "filters": filters,
            "selected_session": selected_session,
            "selected_property": selected_property,
            "selected_messages": selected_messages,

            "pmc_id": (pmc_obj.id if pmc_obj else None),
        },
    )





@router.post("/admin/jobs/refresh-session-status")
def refresh_session_status(request: Request, db: Session = Depends(get_db)):
    token = request.headers.get("x-admin-job-token", "")
    if not ADMIN_JOB_TOKEN or token != ADMIN_JOB_TOKEN:
        return JSONResponse(status_code=401, content={"ok": False, "error": "Unauthorized"})

    cutoff = datetime.utcnow() - timedelta(days=90)
    sessions = db.query(ChatSession).filter(ChatSession.last_activity_at >= cutoff).all()

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
            # no stay dates => true pre-booking/inquiry
            new_status = "pre_booking"
        elif today < a:
            # booked but not yet arrived
            new_status = "post_booking"
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


# ----------------------------
# GitHub helpers
# ----------------------------
def _github_headers() -> Dict[str, str]:
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise HTTPException(status_code=500, detail="GITHUB_TOKEN not set")
    return {
        "Authorization": f"token {token}",  # ✅ more standard
        "Accept": "application/vnd.github+json",
    }


# Save Manual File to GitHub
@router.post("/admin/save-manual")
def save_manual_file(
    request: Request,
    file_path: str = Form(...),
    content: str = Form(...),
    db: Session = Depends(get_db),
):
    file_path = require_file_in_scope(request, db, file_path)

    repo_owner = "rookpenny"
    repo_name = "hostscout_data"
    github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}"

    headers = _github_headers()

    get_response = requests.get(github_api_url, headers=headers)
    if get_response.status_code != 200:
        return HTMLResponse(
            f"<h2>GitHub Fetch Error: {get_response.status_code}<br>{get_response.text}</h2>",
            status_code=404,
        )

    sha = get_response.json().get("sha")
    if not sha:
        return HTMLResponse("<h2>GitHub Fetch Error: missing SHA</h2>", status_code=500)

    encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = {"message": f"Update manual file: {file_path}", "content": encoded_content, "sha": sha}

    put_response = requests.put(github_api_url, headers=headers, json=payload)

    if put_response.status_code in (200, 201):
        from utils.github_sync import ensure_repo
        ensure_repo()

        return HTMLResponse(
            "<h2>Manual saved to GitHub successfully.</h2>"
            "<a href='/auth/dashboard'>Return to Dashboard</a>"
        )

    return HTMLResponse(
        f"<h2>GitHub Save Error: {put_response.status_code}<br>{put_response.text}</h2>",
        status_code=500,
    )


# ✅ moved under /admin
@router.get("/admin/edit-config", response_class=HTMLResponse)
def edit_config(
    request: Request,
    file: str,
    db: Session = Depends(get_db),
):
    file = require_file_in_scope(request, db, file)

    repo_owner = "rookpenny"
    repo_name = "hostscout_data"
    github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file}"

    headers = _github_headers()
    response = requests.get(github_api_url, headers=headers)

    if response.status_code != 200:
        return HTMLResponse(f"<h2>GitHub Error: {response.status_code}<br>{response.text}</h2>", status_code=404)

    data = response.json()
    try:
        content = base64.b64decode(data["content"]).decode("utf-8")
    except Exception:
        return HTMLResponse("<h2>Error decoding file content</h2>", status_code=500)

    return templates.TemplateResponse("editor.html", {"request": request, "file_path": file, "content": content})


# ✅ moved under /admin + fixed function name
@router.get("/admin/edit-file", response_class=HTMLResponse)
def edit_file(
    request: Request,
    file: str,
    db: Session = Depends(get_db),
):
    file = require_file_in_scope(request, db, file)

    repo_owner = "rookpenny"
    repo_name = "hostscout_data"
    github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file}"

    headers = _github_headers()
    response = requests.get(github_api_url, headers=headers)
    if response.status_code != 200:
        return HTMLResponse(f"<h2>GitHub Error: {response.status_code}<br>{response.text}</h2>", status_code=404)

    data = response.json()
    content = base64.b64decode(data["content"]).decode("utf-8")

    return templates.TemplateResponse("editor.html", {"request": request, "file_path": file, "content": content})


@router.post("/admin/save-config")
def save_config_file(
    request: Request,
    file_path: str = Form(...),
    content: str = Form(...),
    db: Session = Depends(get_db),
):
    file_path = require_file_in_scope(request, db, file_path)

    repo_owner = "rookpenny"
    repo_name = "hostscout_data"
    github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}"

    headers = _github_headers()

    get_response = requests.get(github_api_url, headers=headers)
    if get_response.status_code != 200:
        return HTMLResponse(
            f"<h2>GitHub Fetch Error: {get_response.status_code}<br>{get_response.text}</h2>",
            status_code=404,
        )

    sha = get_response.json().get("sha")
    encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = {"message": f"Update config file: {file_path}", "content": encoded_content, "sha": sha}

    put_response = requests.put(github_api_url, headers=headers, json=payload)
    if put_response.status_code in (200, 201):
        from utils.github_sync import ensure_repo
        ensure_repo()

        return HTMLResponse("<h2>Config saved to GitHub successfully.</h2><a href='/auth/dashboard'>Return to Dashboard</a>")

    return HTMLResponse(f"<h2>GitHub Save Error: {put_response.status_code}<br>{put_response.text}</h2>", status_code=500)


@router.post("/admin/save-github-file")
def save_github_file(
    request: Request,
    file_path: str = Form(...),
    content: str = Form(...),
    db: Session = Depends(get_db),
):
    file_path = require_file_in_scope(request, db, file_path)

    repo_owner = "rookpenny"
    repo_name = "hostscout_data"
    github_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}"

    headers = _github_headers()

    get_response = requests.get(github_api_url, headers=headers)
    if get_response.status_code != 200:
        return HTMLResponse(f"<h2>GitHub Fetch Error: {get_response.status_code}<br>{get_response.text}</h2>", status_code=404)

    sha = get_response.json().get("sha")
    encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = {"message": f"Update file: {file_path}", "content": encoded_content, "sha": sha}

    put_response = requests.put(github_api_url, headers=headers, json=payload)
    if put_response.status_code in (200, 201):
        from utils.github_sync import ensure_repo
        ensure_repo()

        return HTMLResponse("<h2>File saved to GitHub successfully.</h2><a href='/auth/dashboard'>Return to Dashboard</a>")

    return HTMLResponse(f"<h2>GitHub Save Error: {put_response.status_code}<br>{put_response.text}</h2>", status_code=500)


# ----------------------------
# PMC + Sync routes (kept as-is)
# ----------------------------
@router.get("/admin/pmc-properties/{pmc_id}")
def pmc_properties(request: Request, pmc_id: int, db: Session = Depends(get_db)):
    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)
    if user_role == "pmc":
        if not pmc_obj or pmc_obj.id != pmc_id:
            raise HTTPException(status_code=403, detail="Forbidden")

    properties = db.query(Property).filter(Property.pmc_id == pmc_id).all()
    return templates.TemplateResponse("pmc_properties.html", {"request": request, "properties": properties, "pmc_id": pmc_id})


@router.get("/admin/new-pmc", response_class=HTMLResponse)
def new_pmc_form(request: Request):
    return templates.TemplateResponse(
        "pmc_form.html",
        {"request": request, "pms_integrations": ["Hostaway", "Guesty", "Lodgify", "Other"], "subscription_plans": ["Free", "Pro", "Enterprise"]},
    )


def get_next_account_id(db: Session) -> str:
    last = db.query(PMC).order_by(sa.cast(PMC.pms_account_id, sa.Integer).desc()).first()
    if not last or not (last.pms_account_id or "").isdigit():
        return "10000"
    return str(int(last.pms_account_id) + 1)


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
    require_super(request, db)

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


@router.post("/admin/sync-all")
def sync_all(request: Request, db: Session = Depends(get_db)):
    require_super(request, db)
    try:
        sync_all_integrations()
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    except Exception as e:
        print(f"[ERROR] Failed to sync all: {e}")
        return RedirectResponse(url="/admin/dashboard?status=error", status_code=303)


@router.post("/admin/sync-properties/{account_id}")
def admin_sync_properties_for_pmc(account_id: str, request: Request, db: Session = Depends(get_db)):
    require_super(request, db)
    try:
        count = sync_properties(account_id)  # keep using old name, no breakage
        pmc = db.query(PMC).filter(PMC.pms_account_id == str(account_id)).first()
        synced_at = pmc.last_synced_at.isoformat() if pmc and pmc.last_synced_at else None
        return JSONResponse({"success": True, "message": f"Synced {count} properties", "synced_at": synced_at})
    except Exception as e:
        print(f"[ERROR] Failed to sync: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# ----------------------------
# Admin GPT chat
# ----------------------------
@router.get("/chat-ui", response_class=HTMLResponse)
def chat_ui(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})


@router.api_route("/admin/chat", methods=["GET", "POST"])
async def chat_combined(request: Request, db: Session = Depends(get_db)):
    require_super(request, db)

    if request.method == "GET":
        return templates.TemplateResponse("chat.html", {"request": request})

    data = await request.json()
    user_message = (data.get("message") or "").strip()
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
                        "Use **markdown formatting** with bold headers, bullets, line breaks, and friendly emojis.\n"
                        "Include Google Maps links when places are mentioned.\n"
                        "Keep replies warm, fun, and helpful — never robotic."
                    ),
                },
                {"role": "user", "content": user_message},
            ],
        )
        reply = response.choices[0].message.content
        return {"reply": reply}
    except Exception as e:
        return {"reply": f"❌ ChatGPT Error: {str(e)}"}


# ----------------------------
# PMC update endpoints
# ----------------------------
@router.post("/admin/update-status")
def update_pmc_status(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    require_super(request, db)

    record_id = payload.get("record_id")
    active = payload.get("active", False)

    if not record_id:
        return JSONResponse(status_code=400, content={"error": "Missing record_id"})

    try:
        pmc = db.query(PMC).filter(PMC.id == record_id).first()
        if not pmc:
            return JSONResponse(status_code=404, content={"error": "PMC not found"})

        pmc.active = bool(active)
        db.commit()
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/admin/update-pmc")
def update_pmc(request: Request, payload: PMCUpdateRequest, db: Session = Depends(get_db)):
    require_super(request, db)

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

        if payload.pms_account_id:
            pmc.pms_account_id = payload.pms_account_id

        pmc.active = bool(payload.active)

        db.add(pmc)
        db.commit()
        return {"success": True}

    except RequestValidationError as ve:
        return JSONResponse(status_code=422, content={"error": ve.errors()})
    except Exception as e:
        db.rollback()
        logging.exception("🔥 Exception during PMC update")
        return JSONResponse(status_code=500, content={"error": str(e)})


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

'''
@router.post("/admin/update-properties")
def update_properties(request: Request, payload: list[dict] = Body(...), db: Session = Depends(get_db)):
    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)
    require_pmc_linked(user_role, pmc_obj)

    try:
        for item in payload:
            prop_id = int(item["id"])
            prop = db.query(Property).filter(Property.id == prop_id).first()
            if not prop:
                continue

            if user_role == "pmc" and prop.pmc_id != pmc_obj.id:
                raise HTTPException(status_code=403, detail="Forbidden property update")

            prop.sandy_enabled = bool(item.get("sandy_enabled", False))

        db.commit()
        return {"success": True}
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
'''

def require_login(request: Request):
    if not request.session.get("user"):
        request.session["post_login_redirect"] = str(request.url.path)
        raise HTTPException(status_code=401, detail="Login required")


TEAM_ROLES = {"owner", "admin", "staff", "ops_manager", "maintenance", "cleaner", "read_only"}

def get_me_email(request: Request) -> str:
    email = get_current_admin_identity(request)
    if not email:
        raise HTTPException(status_code=401, detail="Login required")
    return email.strip().lower()


def require_team_admin(request: Request, db: Session):
    """
    Ensures the current user is a PMC-scoped OWNER or ADMIN.

    Returns:
        (user_role, pmc_obj, pmc_user)

    Raises:
        403 if:
        - superuser tries to manage PMC teams
        - user is not linked to a PMC
        - user is not owner/admin
    """
    user_role, pmc_obj, pmc_user, *_ = get_user_role_and_scope(request, db)

    # Superusers do NOT manage PMC teams
    if user_role == "super":
        raise HTTPException(
            status_code=403,
            detail="Team management is PMC-scoped"
        )

    # Must be a PMC user
    if not pmc_obj or not pmc_user:
        raise HTTPException(
            status_code=403,
            detail="PMC account not linked"
        )

    # Role enforcement
    role = (pmc_user.role or "").lower()
    if role not in {"owner", "admin"}:
        raise HTTPException(
            status_code=403,
            detail="Only owner or admin can manage team members"
        )

    return user_role, pmc_obj, pmc_user



@router.get("/admin/pmc-properties-json/{pmc_id}")
def get_pmc_properties_json(request: Request, pmc_id: int, db: Session = Depends(get_db)):
    user_role, pmc_obj, *_ = get_user_role_and_scope(request, db)
    if user_role == "pmc":
        if not pmc_obj or pmc_obj.id != pmc_id:
            raise HTTPException(status_code=403, detail="Forbidden")

    properties = db.query(Property).filter(Property.pmc_id == pmc_id).all()
    return {
        "properties": [
            {"id": p.id, "property_name": p.property_name, "pms_property_id": p.pms_property_id, "sandy_enabled": p.sandy_enabled}
            for p in properties
        ]
    }
