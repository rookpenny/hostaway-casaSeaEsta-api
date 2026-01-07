from datetime import datetime, timezone
from typing import Optional, Literal
from fastapi import APIRouter, Depends, Query, Request

from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db

router = APIRouter(prefix="/admin/analytics/chat")


# --------------------------------------------------
# Helpers
# --------------------------------------------------
def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _enforce_scope(request: Request, pmc_id: Optional[int]) -> Optional[int]:
    """
    Enforce analytics scope:
    - super users: can query any pmc_id
    - non-super users: locked to their own pmc_id
    """
    user = request.state.user  # REQUIRED by your auth middleware

    if not user:
        raise RuntimeError("request.state.user is required for analytics")

    if user.role != "super":
        return user.pmc_id

    return pmc_id


# --------------------------------------------------
# SUMMARY
# --------------------------------------------------
@router.get("/summary")
def summary(
    request: Request,
    from_ms: int = Query(..., alias="from"),
    to_ms: int = Query(..., alias="to"),
    property_id: Optional[int] = None,
    pmc_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    pmc_id = _enforce_scope(request, pmc_id)

    start = ms_to_dt(from_ms)
    end = ms_to_dt(to_ms)

    row = db.execute(
        text("""
        with base as (
            select *
            from analytics_events
            where ts >= :start
              and ts < :end
              and (:pmc_id::bigint is null or pmc_id = :pmc_id)
              and (:property_id::bigint is null or property_id = :property_id)
        )
        select
            -- sessions
            count(*) filter (
                where event_name = 'chat_session_created'
            ) as sessions_total,

            -- messages (IMPORTANT: sender lives in data.sender)
            count(*) filter (
                where event_name = 'message_sent'
                  and coalesce(data->>'sender', data->>'role') in ('user', 'guest')
            ) as user_messages,

            count(*) filter (
                where event_name = 'message_sent'
                  and coalesce(data->>'sender', data->>'role') in ('assistant', 'bot')
            ) as assistant_messages,

            -- UI events
            count(*) filter (
                where event_name = 'followups_shown'
            ) as followups_shown,

            count(*) filter (
                where event_name = 'followup_click'
            ) as followup_clicks,

            -- reactions
            count(*) filter (
                where event_name = 'reaction_set'
                  and data->>'value' = 'up'
            ) as reactions_up,

            count(*) filter (
                where event_name = 'reaction_set'
                  and data->>'value' = 'down'
            ) as reactions_down
        from base;
        """),
        {
            "start": start,
            "end": end,
            "pmc_id": pmc_id,
            "property_id": property_id,
        },
    ).mappings().first()

    # Always return numbers, never nulls
    return {
        "sessions_total": row["sessions_total"] or 0,
        "user_messages": row["user_messages"] or 0,
        "assistant_messages": row["assistant_messages"] or 0,
        "followups_shown": row["followups_shown"] or 0,
        "followup_clicks": row["followup_clicks"] or 0,
        "reactions_up": row["reactions_up"] or 0,
        "reactions_down": row["reactions_down"] or 0,
    }


# --------------------------------------------------
# TIME SERIES
# --------------------------------------------------
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
    pmc_id = _enforce_scope(request, pmc_id)

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
            ) as bucket
        ),
        filtered as (
            select *
            from analytics_events
            where ts >= :start
              and ts < :end
              and (:pmc_id::bigint is null or pmc_id = :pmc_id)
              and (:property_id::bigint is null or property_id = :property_id)
        ),
        agg as (
            select
                date_trunc('{trunc}', ts) as bucket,
                count(*) filter (where event_name = 'chat_session_created') as sessions,
                count(*) filter (where event_name = 'message_sent') as messages,
                count(*) filter (where event_name = 'followup_click') as followup_clicks
            from filtered
            group by 1
        )
        select
            b.bucket,
            coalesce(a.sessions, 0) as sessions,
            coalesce(a.messages, 0) as messages,
            coalesce(a.followup_clicks, 0) as followup_clicks
        from buckets b
        left join agg a using (bucket)
        order by b.bucket asc;
        """),
        {
            "start": start,
            "end": end,
            "pmc_id": pmc_id,
            "property_id": property_id,
        },
    ).mappings().all()

    labels = []
    sessions = []
    messages = []
    followup_clicks = []

    for r in rows:
        dt = r["bucket"]
        labels.append(
            dt.strftime("%b %d") if bucket == "day" else dt.strftime("%b %d %H:%M")
        )
        sessions.append(int(r["sessions"]))
        messages.append(int(r["messages"]))
        followup_clicks.append(int(r["followup_clicks"]))

    return {
        "labels": labels,
        "series": {
            "sessions": sessions,
            "messages": messages,
            "followup_clicks": followup_clicks,
        },
    }
