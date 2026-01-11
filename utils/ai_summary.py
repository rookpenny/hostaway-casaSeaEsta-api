# utils/ai_summary.py
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional, Tuple, List

from openai import OpenAI
from sqlalchemy.orm import Session
from sqlalchemy import func

# Import your models (adjust import path if needed)
from models import ChatSession, ChatMessage, Property

# ----------------------------
# Config
# ----------------------------
SUMMARY_MODEL = os.getenv("OPENAI_SUMMARY_MODEL", "gpt-4o-mini")
SUMMARY_THROTTLE_SECONDS = int(os.getenv("SUMMARY_THROTTLE_SECONDS", "120"))  # 2 minutes
SUMMARY_MAX_MESSAGES = int(os.getenv("SUMMARY_MAX_MESSAGES", "60"))           # pull last N msgs

client = OpenAI()


def _safe_str(x) -> str:
    return (str(x).strip() if x is not None else "")


def _format_date(d) -> str:
    # Accepts date/datetime/str-ish; returns pretty string or ""
    if not d:
        return ""
    try:
        if hasattr(d, "date"):
            # datetime -> date
            if hasattr(d, "hour"):
                d = d.date()
        return str(d)
    except Exception:
        return _safe_str(d)

def maybe_autosummarize_on_new_guest_message(db: Session, session_id: int) -> None:
    generate_and_store_summary(db=db, session_id=session_id, force=False)

def _build_system_prompt(session: ChatSession, prop: Optional[Property]) -> str:
    # Booking context (as available)
    guest_name = _safe_str(getattr(session, "guest_name", None))
    reservation_status = _safe_str(getattr(session, "reservation_status", None)) or "unknown"
    source = _safe_str(getattr(session, "source", None))
    arrival = _format_date(getattr(session, "arrival_date", None))
    departure = _format_date(getattr(session, "departure_date", None))

    property_name = _safe_str(getattr(prop, "property_name", None)) or "Unknown property"
    property_id = _safe_str(getattr(session, "property_id", None))

    ctx_lines = [
        "You are an operations assistant for a short-term rental host.",
        "",
        "Context (booking + account):",
        f"- Property: {property_name} (property_id={property_id})",
        f"- Guest name: {guest_name or '(unknown)'}",
        f"- Reservation stage: {reservation_status}",
        f"- Source: {source or '(unknown)'}",
        f"- Arrival date: {arrival or '(unknown)'}",
        f"- Departure date: {departure or '(unknown)'}",
        "",
        "Task:",
        "Summarize the conversation for an admin dashboard.",
        "",
        "Return **markdown** with exactly these sections:",
        "1) **What the guest wants**",
        "2) **Key facts** (dates, unit details, constraints)",
        "3) **Risks / sentiment** (urgent/unhappy signals)",
        "4) **Recommended next action** (clear steps)",
        "",
        "Rules:",
        "- Keep it short, scannable, and operational.",
        "- If dates/times are mentioned, repeat them clearly.",
        "- If missing info blocks action, say what to ask the guest for.",
    ]
    return "\n".join(ctx_lines)


def _conversation_text(msgs: List[ChatMessage]) -> str:
    lines = []
    for m in msgs:
        content = (m.content or "").strip()
        if not content:
            continue
        sender = (m.sender or "").strip().upper() or "UNKNOWN"
        lines.append(f"{sender}: {content}")
    return "\n".join(lines).strip()


def should_refresh_summary(
    session: ChatSession,
    last_msg_at: Optional[datetime],
    force: bool = False,
) -> bool:
    """
    True if we should call the model again.
    - force=True overrides all checks (manual button)
    - only runs if there are new messages since last summary
    - throttles re-runs
    - skips resolved chats (unless forced)
    """
    if force:
        return True

    # If chat is resolved, don't keep re-summarizing it automatically
    if bool(getattr(session, "is_resolved", False)):
        return False

    # No messages => nothing to do
    if not last_msg_at:
        return False

    last_sum_at = getattr(session, "ai_summary_updated_at", None)

    # Never summarized before -> run once
    if not last_sum_at:
        return True

    # If no new messages since last summary -> no-op
    # (This is your "avoid unnecessary calls")
    if last_msg_at <= last_sum_at:
        return False

    # Throttle: only allow a refresh every N minutes
    throttle_minutes = int(os.getenv("SUMMARY_THROTTLE_MINUTES", "10"))
    if (datetime.utcnow() - last_sum_at) < timedelta(minutes=throttle_minutes):
        return False

    return True



def generate_and_store_summary(
    db: Session,
    session_id: int,
    force: bool = False,
) -> Tuple[bool, str, Optional[str]]:
    """
    Generates summary if needed and stores on ChatSession.
    Returns: (did_run, summary_text, error_or_none)
    """
    session = db.query(ChatSession).filter(ChatSession.id == int(session_id)).first()
    if not session:
        return False, "", "ChatSession not found"

    prop = db.query(Property).filter(Property.id == session.property_id).first()

    # Pull last N messages (newest first), then reverse to chronological
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(SUMMARY_MAX_MESSAGES)
        .all()
    )
    msgs.reverse()

    last_msg_at = None
    if msgs:
        last_msg_at = getattr(msgs[-1], "created_at", None)

    if not should_refresh_summary(session, last_msg_at, force=force):
        # No-op: return current summary
        existing = (getattr(session, "ai_summary", None) or "").strip()
        return False, existing, None

    convo = _conversation_text(msgs)
    if not convo:
        summary = "**What the guest wants**\n- (No message content)\n"
        session.ai_summary = summary
        session.ai_summary_updated_at = datetime.utcnow()
        if hasattr(session, "updated_at"):
            session.updated_at = datetime.utcnow()
        db.add(session)
        db.commit()
        return True, summary, None

    system_prompt = _build_system_prompt(session, prop)

    try:
        resp = client.chat.completions.create(
            model=SUMMARY_MODEL,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": convo},
            ],
        )
        summary = (resp.choices[0].message.content or "").strip()
        if not summary:
            summary = "**What the guest wants**\n- (No summary generated)\n"
    except Exception as e:
        return False, (getattr(session, "ai_summary", "") or "").strip(), f"Summarization failed: {str(e)}"

    session.ai_summary = summary
    session.ai_summary_updated_at = datetime.utcnow()
    if hasattr(session, "updated_at"):
        session.updated_at = datetime.utcnow()

    db.add(session)
    db.commit()
    return True, summary, None
