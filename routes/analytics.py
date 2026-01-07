from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Any, Dict, List
from datetime import datetime

from database import get_db

router = APIRouter()

@router.post("/analytics/event")
async def ingest_analytics_event(
    request: Request,
    db: Session = Depends(get_db),
):
    payload = await request.json()
    events: List[Dict[str, Any]] = payload.get("events", [])

    if not events:
        return {"ok": True, "inserted": 0}

    user = getattr(request.state, "user", None)

    inserted = 0

    for evt in events:
        # --- enforce PMC scope ---
        if user and not user.is_superuser:
            pmc_id = user.pmc_id
            user_id = user.id
        else:
            pmc_id = evt.get("pmc_id")
            user_id = evt.get("user_id")

        event_name = evt.get("event_name")
        if not event_name:
            continue  # skip bad events, don't fail batch

        db.execute(
            text("""
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
                    :context::jsonb,
                    :data::jsonb
                )
            """),
            {
                "ts": datetime.utcnow(),
                "pmc_id": pmc_id,
                "property_id": evt.get("property_id"),
                "session_id": evt.get("session_id"),
                "user_id": user_id,
                "event_name": event_name,
                "context": {
                    "thread_id": evt.get("thread_id"),
                    "path": evt.get("path"),
                    "ua": evt.get("ua"),
                },
                "data": evt.get("data", {}),
            },
        )
        inserted += 1

    db.commit()
    return {"ok": True, "inserted": inserted}
