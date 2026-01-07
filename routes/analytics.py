from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Any, Dict, List
from datetime import datetime

from database import get_db

router = APIRouter()

INSERT_EVENT_SQL = text("""
    insert into analytics_events (
        ts,
        pmc_id,
        property_id,
        session_id,
        user_id,
        event_name,
        context,
        data
    )
    values (
        :ts,
        :pmc_id,
        :property_id,
        :session_id,
        :user_id,
        :event_name,
        :context,
        :data
    )
""")

@router.post("/analytics/event")
async def ingest_analytics_event(
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "error": "Invalid JSON"}

    # ✅ Accept BOTH shapes:
    # 1) { events: [ ... ] }
    # 2) { event_name: "...", ... }  (single event)
    events: List[Dict[str, Any]] = []

    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        events = [e for e in payload["events"] if isinstance(e, dict)]
    elif isinstance(payload, dict) and payload.get("event_name"):
        events = [payload]
    else:
        return {"ok": True, "inserted": 0}

    user = getattr(request.state, "user", None)
    now = datetime.utcnow()
    inserted = 0

    for evt in events:
        event_name = evt.get("event_name")
        if not event_name:
            continue

        # --- enforce PMC scope ---
        if user and not getattr(user, "is_superuser", False):
            pmc_id = getattr(user, "pmc_id", None)
            user_id = getattr(user, "id", None)
        else:
            pmc_id = evt.get("pmc_id")
            user_id = evt.get("user_id")

        context = evt.get("context") or {
            "thread_id": evt.get("thread_id"),
            "path": evt.get("path"),
            "ua": evt.get("ua"),
        }
        if not isinstance(context, dict):
            context = {"_raw": context}

        data = evt.get("data") or {}
        if not isinstance(data, dict):
            data = {"_raw": data}

        db.execute(
            INSERT_EVENT_SQL,
            {
                "ts": now,
                "pmc_id": pmc_id,
                "property_id": evt.get("property_id"),
                "session_id": evt.get("session_id"),
                "user_id": user_id,
                "event_name": event_name,
                "context": context,
                "data": data,
            },
        )
        inserted += 1

    db.commit()
    return {"ok": True, "inserted": inserted}
