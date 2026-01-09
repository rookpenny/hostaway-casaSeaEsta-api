import json
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Any, Dict, List
from datetime import datetime

from database import get_db

router = APIRouter()

GET_PMC_FOR_PROPERTY = text("""
    select pmc_id
    from properties
    where id = :property_id
    limit 1
""")

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
        cast(:context as jsonb),
        cast(:data as jsonb)
    )
""")

def _get_pmc_id_for_property(db: Session, property_id: int) -> int:
    row = db.execute(GET_PMC_FOR_PROPERTY, {"property_id": int(property_id)}).first()
    if not row or row[0] is None:
        raise HTTPException(status_code=400, detail="Invalid property_id")
    return int(row[0])

@router.post("/analytics/event")
async def ingest_analytics_event(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()

    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        events = [e for e in payload["events"] if isinstance(e, dict)]
    elif isinstance(payload, dict) and payload.get("event_name"):
        events = [payload]
    else:
        return {"ok": True, "inserted": 0}

    now = datetime.utcnow()
    inserted = 0

    # session identity (admin-side emits can be attributed)
    role = (request.session.get("role") or "").strip().lower()
    sess_pmc_id = request.session.get("pmc_id")
    sess_pmc_user_id = request.session.get("pmc_user_id")

    for evt in events:
        event_name = evt.get("event_name")
        property_id = evt.get("property_id")
        if not event_name or not property_id:
            continue

        # Always resolve pmc_id from property_id (prevents spoofing)
        pmc_id = _get_pmc_id_for_property(db, int(property_id))

        # user_id only if admin session exists; guests are anonymous
        user_id = int(sess_pmc_user_id) if sess_pmc_user_id else None

        context = evt.get("context") or {
            "thread_id": evt.get("thread_id"),
            "path": evt.get("path"),
            "ua": evt.get("ua"),
        }
        if not isinstance(context, dict):
            context = {"_raw": context}

        # add server-derived metadata (better than trusting client)
        context.setdefault("server_ua", request.headers.get("user-agent"))
        context.setdefault("referer", request.headers.get("referer"))

        data = evt.get("data") or {}
        if not isinstance(data, dict):
            data = {"_raw": data}

        db.execute(
            INSERT_EVENT_SQL,
            {
                "ts": now,
                "pmc_id": pmc_id,
                "property_id": int(property_id),
                "session_id": evt.get("session_id"),
                "user_id": user_id,
                "event_name": event_name,
                "context": json.dumps(context),
                "data": json.dumps(data),
            },
        )
        inserted += 1

    db.commit()
    return {"ok": True, "inserted": inserted}
