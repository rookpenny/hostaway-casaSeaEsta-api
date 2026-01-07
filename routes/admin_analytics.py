from datetime import datetime, timezone
from typing import Optional, Literal
from fastapi import APIRouter, Depends, Query, Request

from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db

router = APIRouter(prefix="/admin/analytics/chat")

def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)

@router.get("/summary")
def summary(
    request: Request,
    from_ms: int = Query(..., alias="from"),
    to_ms: int = Query(..., alias="to"),
    property_id: Optional[int] = None,
    pmc_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    user = request.state.user  # REQUIRED (your auth middleware must set this)

    # 🔒 ENFORCE SCOPE
    if user.role != "super":
        pmc_id = user.pmc_id

    start = ms_to_dt(from_ms)
    end = ms_to_dt(to_ms)

    row = db.execute(
        text("""
        with base as (
          select *
          from analytics_events
          where ts >= :start and ts < :end
            and (:pmc_id::bigint is null or pmc_id = :pmc_id)
            and (:property_id::bigint is null or property_id = :property_id)
        )
        select
          count(*) filter (where event_name = 'chat_session_created') as sessions_total,

          -- IMPORTANT: your client sends data.sender, not data.role
          count(*) filter (
            where event_name = 'message_sent'
              and coalesce(data->>'sender', data->>'role') = 'user'
          ) as user_messages,

          count(*) filter (
            where event_name = 'message_sent'
              and coalesce(data->>'sender', data->>'role') = 'assistant'
          ) as assistant_messages,

          count(*) filter (where event_name = 'followup_click') as followup_click,
          count(*) filter (where event_name = 'reaction_set' and data->>'value' = 'up') as reactions_up,
          count(*) filter (where event_name = 'reaction_set' and data->>'value' = 'down') as reactions_down
        from base;
        """),
        {
            "start": start,
            "end": end,
            "pmc_id": pmc_id,
            "property_id": property_id,
        },
    ).mappings().first()

    return dict(row or {})


@router.get("/timeseries")
def timeseries(
    request: Request,
    bucket: Literal["day", "hour"] = "day",
    from_ms: int = Query(..., alias="from"),
    to_ms: int = Query(..., alias="to"),
    property_id: Optional[int] = None,
    pmc_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    user = request.state.user  # REQUIRED

    # 🔒 ENFORCE SCOPE
    if user.role != "super":
        pmc_id = user.pmc_id

    start = ms_to_dt(from_ms)
    end = ms_to_dt(to_ms)
    trunc = "day" if bucket == "day" else "hour"

    rows = db.execute(
        text(f"""
        with buckets as (
          select generate_series(
            date_trunc('{trunc}', :start::timestamptz),
            date_trunc('{trunc}', :end::timestamptz),
            interval '1 {trunc}'
          ) as b
        ),
        filtered as (
          select *
          from analytics_events
          where ts >= :start and ts < :end
            and (:pmc_id::bigint is null or pmc_id = :pmc_id)
            and (:property_id::bigint is null or property_id = :property_id)
        ),
        agg as (
          select
            date_trunc('{trunc}', ts) as b,
            count(*) filter (where event_name = 'chat_session_created') as sessions,
            count(*) filter (where event_name = 'message_sent') as messages,
            count(*) filter (where event_name = 'followup_click') as followup_click
          from filtered
          group by 1
        )
        select
          buckets.b as bucket,
          coalesce(agg.sessions, 0) as sessions,
          coalesce(agg.messages, 0) as messages,
          coalesce(agg.followup_click, 0) as followup_click
        from buckets
        left join agg using (b)
        order by buckets.b asc;
        """),
        {
            "start": start,
            "end": end,
            "pmc_id": pmc_id,
            "property_id": property_id,
        },
    ).mappings().all()

    labels, sessions, messages, followup_click = [], [], [], []

    for r in rows:
        dt = r["bucket"]
        labels.append(dt.strftime("%b %d") if bucket == "day" else dt.strftime("%b %d %H:%M"))
        sessions.append(int(r["sessions"]))
        messages.append(int(r["messages"]))
        followup_click.append(int(r["followup_click"]))

    return {
        "labels": labels,
        "series": {
            "sessions": sessions,
            "messages": messages,
            "followup_click": followup_click,
        },
    }
