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
    # Be tolerant of bad payloads
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "error": "Invalid JSON"}

    events: List[Dict[str, Any]] = payload.get("events", [])
    if not isinstance(events, list) or not events:
        return {"ok": True, "inserted": 0}

    user = getattr(request.state, "user", None)

    inserted = 0
    now = datetime.utcnow()

    for evt in events:
        if not isinstance(evt, dict):
            continue

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

        # Keep context small/consistent
        context = {
            "thread_id": evt.get("thread_id"),
            "path": evt.get("path"),
            "ua": evt.get("ua"),
        }

        data = evt.get("data") or {}
        if not isinstance(data, dict):
            data = {"_raw": data}

        db.execute(
            INSERT_EVENT_SQL,
            {
                "ts": now,  # optional; table default works too
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
