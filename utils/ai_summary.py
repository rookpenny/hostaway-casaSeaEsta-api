from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from sqlalchemy.orm import Session

from models import ChatSession, ChatMessage, Property

SUMMARY_MODEL = os.getenv("OPENAI_SUMMARY_MODEL", "gpt-4o-mini")
SUMMARY_MAX_MESSAGES = int(os.getenv("SUMMARY_MAX_MESSAGES", "60"))
SUMMARY_THROTTLE_MINUTES = int(os.getenv("SUMMARY_THROTTLE_MINUTES", "10"))

client = OpenAI()


def _safe_str(x: Any) -> str:
    return str(x).strip() if x is not None else ""


def _format_date(d: Any) -> str:
    if not d:
        return ""
    try:
        if hasattr(d, "date") and hasattr(d, "hour"):
            d = d.date()
        return str(d)
    except Exception:
        return _safe_str(d)


def _set_if_attr(obj: Any, attr: str, value: Any) -> None:
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def maybe_autosummarize_on_new_guest_message(db: Session, session_id: int) -> None:
    generate_and_store_summary(db=db, session_id=session_id, force=False)


def _build_system_prompt(session: ChatSession, prop: Optional[Property]) -> str:
    now = datetime.utcnow()
    today_str = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M UTC")

    guest_name = _safe_str(getattr(session, "guest_name", None))
    reservation_status = _safe_str(getattr(session, "reservation_status", None)) or "unknown"
    source = _safe_str(getattr(session, "source", None))
    arrival = _format_date(getattr(session, "arrival_date", None))
    departure = _format_date(getattr(session, "departure_date", None))

    property_name = _safe_str(getattr(prop, "property_name", None)) or "Unknown property"
    property_id = _safe_str(getattr(session, "property_id", None))

    return f"""
You are an expert short-term rental operations analyst.

Analyze the guest conversation for an admin dashboard.

CONTEXT
- Current date: {today_str}
- Current time: {current_time}
- Property: {property_name} (property_id={property_id})
- Guest name: {guest_name or "(unknown)"}
- Reservation stage: {reservation_status}
- Source: {source or "(unknown)"}
- Arrival date: {arrival or "(unknown)"}
- Departure date: {departure or "(unknown)"}

GOALS
1. Identify what the guest actually wants
2. Identify key facts and constraints
3. Identify urgency / risk / unhappiness
4. Identify whether this is likely informational, operational, or escalatory
5. Recommend the clearest next action

IMPORTANT RULES
- Be concise, practical, and operational
- Think like an STR ops lead, not a marketer
- Do not add fluff
- Repeat any important dates/times clearly
- If missing information blocks action, say exactly what is missing
- If this feels like a repeatable issue, frame it as a trend
- If this feels like a human-handoff situation, say so
- Return ONLY valid JSON
- Do not wrap the JSON in markdown fences

Return JSON in exactly this shape:

{{
  "summary_markdown": "## What the guest wants\\n...\\n\\n## Key facts\\n...\\n\\n## Risks / sentiment\\n...\\n\\n## Recommended next action\\n...",
  "trend_label": "short label",
  "trend_detail": "1-2 sentence explanation",
  "recommendation_label": "short action title",
  "recommendation_detail": "clear next step",
  "severity": "low",
  "needs_human": false,
  "guest_intent": "short_snake_case_label",
  "ops_category": "arrival",
  "missing_info": [],
  "confidence": 0.84
}}

Allowed severity values: "low", "medium", "high"
Allowed ops_category examples: "arrival", "access", "parking", "cleaning", "maintenance", "payments", "noise", "general"

The summary_markdown must always use exactly these headings:
## What the guest wants
## Key facts
## Risks / sentiment
## Recommended next action
""".strip()


def _conversation_text(msgs: List[ChatMessage]) -> str:
    lines: List[str] = []
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
    if force:
        return True

    if bool(getattr(session, "is_resolved", False)):
        return False

    if not last_msg_at:
        return False

    last_sum_at = getattr(session, "ai_summary_updated_at", None)

    if not last_sum_at:
        return True

    if last_msg_at <= last_sum_at:
        return False

    if (datetime.utcnow() - last_sum_at) < timedelta(minutes=SUMMARY_THROTTLE_MINUTES):
        return False

    return True


def _fallback_summary_payload(reason: str = "") -> Dict[str, Any]:
    detail = reason.strip() or "The AI could not generate structured insights."
    return {
        "summary_markdown": (
            "## What the guest wants\n"
            "- Unable to confidently extract from the conversation.\n\n"
            "## Key facts\n"
            f"- {detail}\n\n"
            "## Risks / sentiment\n"
            "- Unknown.\n\n"
            "## Recommended next action\n"
            "- Review the thread manually."
        ),
        "trend_label": "Needs manual review",
        "trend_detail": detail,
        "recommendation_label": "Review manually",
        "recommendation_detail": "Check the conversation directly and decide whether this needs a guide update, automation, or ops follow-up.",
        "severity": "low",
        "needs_human": True,
        "guest_intent": "unknown",
        "ops_category": "general",
        "missing_info": [],
        "confidence": 0.35,
    }


def _normalize_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    fallback = _fallback_summary_payload()

    summary_markdown = _safe_str(data.get("summary_markdown")) or fallback["summary_markdown"]
    trend_label = _safe_str(data.get("trend_label")) or "Pattern identified"
    trend_detail = _safe_str(data.get("trend_detail")) or "This conversation reflects a guest need or operational moment worth tracking."
    recommendation_label = _safe_str(data.get("recommendation_label")) or "Monitor and refine"
    recommendation_detail = _safe_str(data.get("recommendation_detail")) or "Review whether this should become a guide update, automation, or ops follow-up."

    severity = _safe_str(data.get("severity")).lower()
    if severity not in {"low", "medium", "high"}:
        severity = "low"

    ops_category = _safe_str(data.get("ops_category")).lower() or "general"
    guest_intent = _safe_str(data.get("guest_intent")).lower() or "unknown"

    missing_info = data.get("missing_info")
    if not isinstance(missing_info, list):
        missing_info = []

    try:
        confidence = float(data.get("confidence", 0.5))
    except Exception:
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    needs_human = bool(data.get("needs_human", False))

    return {
        "summary_markdown": summary_markdown,
        "trend_label": trend_label,
        "trend_detail": trend_detail,
        "recommendation_label": recommendation_label,
        "recommendation_detail": recommendation_detail,
        "severity": severity,
        "needs_human": needs_human,
        "guest_intent": guest_intent,
        "ops_category": ops_category,
        "missing_info": missing_info,
        "confidence": confidence,
    }


def _empty_conversation_payload() -> Dict[str, Any]:
    return {
        "summary_markdown": (
            "## What the guest wants\n"
            "- No message content.\n\n"
            "## Key facts\n"
            "- No usable conversation text was found.\n\n"
            "## Risks / sentiment\n"
            "- None detected.\n\n"
            "## Recommended next action\n"
            "- Wait for more guest context."
        ),
        "trend_label": "No conversation yet",
        "trend_detail": "There was not enough message content to identify a pattern.",
        "recommendation_label": "Wait for more context",
        "recommendation_detail": "No action needed until the guest sends more information.",
        "severity": "low",
        "needs_human": False,
        "guest_intent": "none",
        "ops_category": "general",
        "missing_info": [],
        "confidence": 1.0,
    }


def _call_summary_model(system_prompt: str, convo: str) -> Dict[str, Any]:
    resp = client.chat.completions.create(
        model=SUMMARY_MODEL,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": convo},
        ],
    )

    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        return _fallback_summary_payload("The model returned an empty response.")

    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return _fallback_summary_payload("The model returned non-object JSON.")
        return _normalize_payload(parsed)
    except Exception:
        return _fallback_summary_payload("The model returned invalid JSON.")


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

    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(SUMMARY_MAX_MESSAGES)
        .all()
    )
    msgs.reverse()

    last_msg_at = getattr(msgs[-1], "created_at", None) if msgs else None

    if not should_refresh_summary(session, last_msg_at, force=force):
        existing = (getattr(session, "ai_summary", None) or "").strip()
        return False, existing, None

    convo = _conversation_text(msgs)

    if not convo:
        payload = _empty_conversation_payload()
    else:
        system_prompt = _build_system_prompt(session, prop)
        try:
            payload = _call_summary_model(system_prompt, convo)
        except Exception as e:
            return (
                False,
                (getattr(session, "ai_summary", "") or "").strip(),
                f"Summarization failed: {str(e)}",
            )

    now = datetime.utcnow()

    session.ai_summary = payload["summary_markdown"]
    session.ai_summary_updated_at = now

    _set_if_attr(session, "signal_label", payload["trend_label"])
    _set_if_attr(session, "signal_detail", payload["trend_detail"])
    _set_if_attr(session, "recommendation_label", payload["recommendation_label"])
    _set_if_attr(session, "recommendation_detail", payload["recommendation_detail"])
    _set_if_attr(session, "severity", payload["severity"])
    _set_if_attr(session, "needs_human", payload["needs_human"])
    _set_if_attr(session, "guest_intent", payload["guest_intent"])
    _set_if_attr(session, "ops_category", payload["ops_category"])
    _set_if_attr(session, "summary_confidence", payload["confidence"])

    if hasattr(session, "updated_at"):
        session.updated_at = now

    db.add(session)
    db.commit()

    return True, payload["summary_markdown"], None
