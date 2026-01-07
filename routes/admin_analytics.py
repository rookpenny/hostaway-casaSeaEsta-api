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
    user = getattr(request.state, "user", None)
    if not user:
        raise RuntimeError("request.state.user is required for analytics")

    if user.role != "super":
        return user.pmc_id

    return pmc_id


# --------------------------------------------------
# SUMMARY (now includes conversion + avg response time)
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
        ),
        msg_base as (
            select
                ts,
                session_id,
                coalesce(data->>'sender', data->>'role') as sender,
                coalesce(data->>'assistant', data->>'variant', 'default') as assistant_key
            from base
            where event_name = 'message_sent'
        ),
        user_msgs as (
            select session_id, ts as user_ts
            from msg_base
            where sender in ('user', 'guest')
        ),
        response_pairs as (
            select
                u.session_id,
                u.user_ts,
                a.assistant_ts,
                extract(epoch from (a.assistant_ts - u.user_ts)) as response_seconds
            from user_msgs u
            join lateral (
                select mb.ts as assistant_ts
                from msg_base mb
                where mb.session_id = u.session_id
                  and mb.ts > u.user_ts
                  and mb.sender in ('assistant', 'bot')
                order by mb.ts asc
                limit 1
            ) a on true
            where a.assistant_ts is not null
        ),
        response_clean as (
            select *
            from response_pairs
            where response_seconds is not null
              and response_seconds >= 0
              and response_seconds <= 1800 -- cap at 30 min to avoid skew when a reply never came
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

            -- followups
            count(*) filter (
                where event_name = 'followups_shown'
            ) as followups_shown,

            count(*) filter (
                where event_name = 'followup_click'
            ) as followup_clicks,

            -- conversion
            (
              count(*) filter (where event_name = 'followup_click')::float
              / nullif(count(*) filter (where event_name = 'followups_shown')::float, 0)
            ) as followup_conversion_rate,

            -- reactions
            count(*) filter (
                where event_name = 'reaction_set'
                  and data->>'value' = 'up'
            ) as reactions_up,

            count(*) filter (
                where event_name = 'reaction_set'
                  and data->>'value' = 'down'
            ) as reactions_down,

            -- response time (seconds)
            (select avg(response_seconds) from response_clean) as avg_response_seconds,
            (select percentile_cont(0.5) within group (order by response_seconds) from response_clean) as p50_response_seconds
        from base;
        """),
        {
            "start": start,
            "end": end,
            "pmc_id": pmc_id,
            "property_id": property_id,
        },
    ).mappings().first() or {}

    def _num(x, default=0):
        return default if x is None else x

    return {
        "sessions_total": int(_num(row.get("sessions_total"), 0)),
        "user_messages": int(_num(row.get("user_messages"), 0)),
        "assistant_messages": int(_num(row.get("assistant_messages"), 0)),
        "followups_shown": int(_num(row.get("followups_shown"), 0)),
        "followup_clicks": int(_num(row.get("followup_clicks"), 0)),
        "followup_conversion_rate": float(_num(row.get("followup_conversion_rate"), 0.0)),
        "reactions_up": int(_num(row.get("reactions_up"), 0)),
        "reactions_down": int(_num(row.get("reactions_down"), 0)),
        "avg_response_seconds": float(_num(row.get("avg_response_seconds"), 0.0)),
        "p50_response_seconds": float(_num(row.get("p50_response_seconds"), 0.0)),
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
                count(*) filter (where event_name = 'followup_click') as followup_clicks,
                count(*) filter (where event_name = 'followups_shown') as followups_shown
            from filtered
            group by 1
        )
        select
            b.bucket,
            coalesce(a.sessions, 0) as sessions,
            coalesce(a.messages, 0) as messages,
            coalesce(a.followup_clicks, 0) as followup_clicks,
            coalesce(a.followups_shown, 0) as followups_shown
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

    labels, sessions, messages, followup_clicks, followups_shown = [], [], [], [], []

    for r in rows:
        dt = r["bucket"]
        labels.append(dt.strftime("%b %d") if bucket == "day" else dt.strftime("%b %d %H:%M"))
        sessions.append(int(r["sessions"]))
        messages.append(int(r["messages"]))
        followup_clicks.append(int(r["followup_clicks"]))
        followups_shown.append(int(r["followups_shown"]))

    return {
        "labels": labels,
        "series": {
            "sessions": sessions,
            "messages": messages,
            "followup_clicks": followup_clicks,
            "followups_shown": followups_shown,
        },
    }


# --------------------------------------------------
# 🔥 TOP PROPERTIES
# --------------------------------------------------
@router.get("/top-properties")
def top_properties(
    request: Request,
    from_ms: int = Query(..., alias="from"),
    to_ms: int = Query(..., alias="to"),
    pmc_id: Optional[int] = None,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    pmc_id = _enforce_scope(request, pmc_id)
    start = ms_to_dt(from_ms)
    end = ms_to_dt(to_ms)

    rows = db.execute(
        text("""
        with base as (
          select *
          from analytics_events
          where ts >= :start and ts < :end
            and (:pmc_id::bigint is null or pmc_id = :pmc_id)
            and property_id is not null
        ),
        agg as (
          select
            property_id,
            count(*) filter (where event_name = 'chat_session_created') as sessions,
            count(*) filter (where event_name = 'message_sent') as messages,
            count(*) filter (where event_name = 'followups_shown') as followups_shown,
            count(*) filter (where event_name = 'followup_click') as followup_clicks
          from base
          group by 1
        )
        select
          property_id,
          sessions,
          messages,
          followups_shown,
          followup_clicks,
          (followup_clicks::float / nullif(followups_shown::float, 0)) as conversion_rate
        from agg
        order by sessions desc, messages desc
        limit :limit;
        """),
        {"start": start, "end": end, "pmc_id": pmc_id, "limit": limit},
    ).mappings().all()

    return {"rows": [dict(r) for r in rows]}


# --------------------------------------------------
# 📊 CONVERSION RATE (standalone)
# --------------------------------------------------
@router.get("/conversion")
def conversion(
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
          where ts >= :start and ts < :end
            and (:pmc_id::bigint is null or pmc_id = :pmc_id)
            and (:property_id::bigint is null or property_id = :property_id)
        )
        select
          count(*) filter (where event_name = 'followups_shown') as followups_shown,
          count(*) filter (where event_name = 'followup_click') as followup_clicks,
          (count(*) filter (where event_name = 'followup_click')::float
            / nullif(count(*) filter (where event_name = 'followups_shown')::float, 0)
          ) as conversion_rate;
        """),
        {"start": start, "end": end, "pmc_id": pmc_id, "property_id": property_id},
    ).mappings().first() or {}

    return {
        "followups_shown": int(row.get("followups_shown") or 0),
        "followup_clicks": int(row.get("followup_clicks") or 0),
        "conversion_rate": float(row.get("conversion_rate") or 0.0),
    }


# --------------------------------------------------
# ⏱ AVG RESPONSE TIME
# --------------------------------------------------
@router.get("/response-time")
def response_time(
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
          where ts >= :start and ts < :end
            and (:pmc_id::bigint is null or pmc_id = :pmc_id)
            and (:property_id::bigint is null or property_id = :property_id)
        ),
        msg_base as (
          select
            ts,
            session_id,
            coalesce(data->>'sender', data->>'role') as sender
          from base
          where event_name = 'message_sent'
            and session_id is not null
        ),
        user_msgs as (
          select session_id, ts as user_ts
          from msg_base
          where sender in ('user','guest')
        ),
        pairs as (
          select
            u.session_id,
            u.user_ts,
            a.assistant_ts,
            extract(epoch from (a.assistant_ts - u.user_ts)) as response_seconds
          from user_msgs u
          join lateral (
            select mb.ts as assistant_ts
            from msg_base mb
            where mb.session_id = u.session_id
              and mb.ts > u.user_ts
              and mb.sender in ('assistant','bot')
            order by mb.ts asc
            limit 1
          ) a on true
        ),
        clean as (
          select *
          from pairs
          where response_seconds is not null
            and response_seconds >= 0
            and response_seconds <= 1800
        )
        select
          count(*) as samples,
          avg(response_seconds) as avg_seconds,
          percentile_cont(0.5) within group (order by response_seconds) as p50_seconds,
          percentile_cont(0.9) within group (order by response_seconds) as p90_seconds
        from clean;
        """),
        {"start": start, "end": end, "pmc_id": pmc_id, "property_id": property_id},
    ).mappings().first() or {}

    return {
        "samples": int(row.get("samples") or 0),
        "avg_seconds": float(row.get("avg_seconds") or 0.0),
        "p50_seconds": float(row.get("p50_seconds") or 0.0),
        "p90_seconds": float(row.get("p90_seconds") or 0.0),
    }


# --------------------------------------------------
# 🧠 PER-ASSISTANT PERFORMANCE
# (groups by data.assistant OR data.variant OR 'default')
# --------------------------------------------------
@router.get("/assistant-performance")
def assistant_performance(
    request: Request,
    from_ms: int = Query(..., alias="from"),
    to_ms: int = Query(..., alias="to"),
    property_id: Optional[int] = None,
    pmc_id: Optional[int] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    pmc_id = _enforce_scope(request, pmc_id)
    start = ms_to_dt(from_ms)
    end = ms_to_dt(to_ms)

    rows = db.execute(
        text("""
        with base as (
          select *
          from analytics_events
          where ts >= :start and ts < :end
            and (:pmc_id::bigint is null or pmc_id = :pmc_id)
            and (:property_id::bigint is null or property_id = :property_id)
        ),
        msg_base as (
          select
            ts,
            session_id,
            coalesce(data->>'sender', data->>'role') as sender,
            coalesce(data->>'assistant', data->>'variant', 'default') as assistant_key
          from base
          where event_name = 'message_sent'
            and session_id is not null
        ),
        user_msgs as (
          select session_id, ts as user_ts
          from msg_base
          where sender in ('user','guest')
        ),
        pairs as (
          select
            u.session_id,
            u.user_ts,
            a.assistant_ts,
            a.assistant_key,
            extract(epoch from (a.assistant_ts - u.user_ts)) as response_seconds
          from user_msgs u
          join lateral (
            select mb.ts as assistant_ts, mb.assistant_key
            from msg_base mb
            where mb.session_id = u.session_id
              and mb.ts > u.user_ts
              and mb.sender in ('assistant','bot')
            order by mb.ts asc
            limit 1
          ) a on true
        ),
        clean as (
          select *
          from pairs
          where response_seconds is not null
            and response_seconds >= 0
            and response_seconds <= 1800
        ),
        msgs as (
          select
            assistant_key,
            count(*) filter (where sender in ('assistant','bot')) as assistant_messages,
            count(*) filter (where sender in ('user','guest')) as user_messages
          from msg_base
          group by 1
        ),
        rt as (
          select
            assistant_key,
            count(*) as response_samples,
            avg(response_seconds) as avg_seconds,
            percentile_cont(0.5) within group (order by response_seconds) as p50_seconds
          from clean
          group by 1
        )
        select
          coalesce(m.assistant_key, r.assistant_key) as assistant_key,
          coalesce(m.user_messages, 0) as user_messages,
          coalesce(m.assistant_messages, 0) as assistant_messages,
          coalesce(r.response_samples, 0) as response_samples,
          coalesce(r.avg_seconds, 0) as avg_response_seconds,
          coalesce(r.p50_seconds, 0) as p50_response_seconds
        from msgs m
        full join rt r using (assistant_key)
        order by assistant_messages desc, response_samples desc
        limit :limit;
        """),
        {
            "start": start,
            "end": end,
            "pmc_id": pmc_id,
            "property_id": property_id,
            "limit": limit,
        },
    ).mappings().all()

    return {"rows": [dict(r) for r in rows]}
