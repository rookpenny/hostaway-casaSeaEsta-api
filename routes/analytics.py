from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Any, Dict

from database import get_db

router = APIRouter()

@router.post("/analytics/event")
def ingest_analytics_event(
    request: Request,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    """
    Ingest analytics events from chat / UI.
    Scope is enforced server-side:
      - hosts -> their own pmc_id only
      - super -> any pmc_id
    """

    user = getattr(request.state, "user", None)

    # --- enforce PMC scope ---
    if user and not user.is_superuser:
        pmc_id = user.pmc_id
        user_id = user.id
    else:
        pmc_id = payload.get("pmc_id")
        user_id = payload.get("user_id")

    # --- extract safe fields ---
    event_name = payload.get("event_name")
    if not event_name:
        return {"ok": False, "error": "event_name required"}

    db.execute(
        text("""
            insert into analytics_events (
                pmc_id,
                property_id,
                session_id,
                user_id,
                event_name,
                context,
                data
            )
            values (
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
            "pmc_id": pmc_id,
            "property_id": payload.get("property_id"),
            "session_id": payload.get("session_id"),
            "user_id": user_id,
            "event_name": event_name,
            "context": payload.get("context", {}),
            "data": payload.get("data", {}),
        },
    )

    db.commit()
    return {"ok": True}
