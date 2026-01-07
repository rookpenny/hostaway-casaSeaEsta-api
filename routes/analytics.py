from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db

router = APIRouter()

class AnalyticsEventIn(BaseModel):
    event_name: str

    # common context fields (present if baseAnalyticsContext includes them)
    pmc_id: Optional[int] = None
    property_id: Optional[int] = None
    session_id: Optional[int] = None
    user_id: Optional[int] = None

    # allow anything else in baseAnalyticsContext without breaking
    context: Dict[str, Any] = Field(default_factory=dict)
    data: Dict[str, Any] = Field(default_factory=dict)

@router.post("/analytics/event")
def ingest_event(payload: AnalyticsEventIn, request: Request, db: Session = Depends(get_db)):
    user = request.state.user  # however you already attach user

    # 🔐 enforce scope
    if user.role != "super":
        pmc_id = user.pmc_id
    else:
        pmc_id = payload.pmc_id  # super can send / override

    db.execute(
        text("""
          insert into analytics_events (
            event_name,
            pmc_id,
            property_id,
            session_id,
            user_id,
            context,
            data
          )
          values (
            :event_name,
            :pmc_id,
            :property_id,
            :session_id,
            :user_id,
            :context::jsonb,
            :data::jsonb
          )
        """),
        {
            "event_name": payload.event_name,
            "pmc_id": pmc_id,
            "property_id": payload.property_id,
            "session_id": payload.session_id,
            "user_id": user.id if user.is_authenticated else None,
            "context": payload.context,
            "data": payload.data,
        },
    )
    db.commit()
    return {"ok": True}

