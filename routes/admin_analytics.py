from datetime import datetime, timezone
from typing import Optional, Literal
from fastapi import APIRouter, Depends, Query, Request, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text, bindparam
from sqlalchemy.dialects.postgresql import ARRAY, TEXT

from database import get_db

router = APIRouter(prefix="/admin/analytics/chat")


# --------------------------------------------------
# EVENT NAME CONFIG (edit to match what you emit)
# --------------------------------------------------
FOLLOWUPS_SHOWN_EVENT = "followups_shown"
FOLLOWUP_CLICK_EVENT = "followup_click"

# Errors + escalation
CHAT_ERROR_EVENT = "chat_error"
CONTACT_HOST_CLICK_EVENT = "contact_host_click"

# Upgrade funnel (keep for later; safe to leave even if you emit 0)
UPGRADE_START_EVENTS = (
    "upgrade_checkout_started",
    "upgrade_checkout_created",
    "upgrade_checkout",
)

UPGRADE_PURCHASE_EVENTS = (
    "upgrade_purchase_succeeded",
    "upgrade_purchase_completed",
    "upgrade_paid",
    "upgrade_payment_succeeded",
)


# --------------------------------------------------
# Helpers
# --------------------------------------------------
def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _is_super(role: Optional[str]) -> bool:
    r = (role or "").strip().lower()
    return r in ("super", "superuser")

def _enforce_scope(request: Request, pmc_id: Optional[int]) -> Optional[int]:
    role = request.session.get("role")
    if _is_super(role):
        return pmc_id

    if (role or "").strip().lower() == "pmc":
        sess_pmc_id = request.session.get("pmc_id")
        if not sess_pmc_id:
            raise HTTPException(status_code=401, detail="Missing pmc_id in session")
        return int(sess_pmc_id)

    raise HTTPException(status_code=403, detail="Forbidden")




def _num(x, default=0):
    return default if x is None else x


def _assert_property_in_pmc(db: Session, property_id: int, pmc_id: int) -> None:
    ok = db.execute(
        text("select 1 from properties where id=:pid and pmc_id=:pmc limit 1"),
        {"pid": int(property_id), "pmc": int(pmc_id)},
    ).first()
    if not ok:
        # property doesn't exist or not in scope
        raise HTTPException(status_code=403, detail="Forbidden property scope")



# --------------------------------------------------
# SUMMARY
# Adds:
# - response rate (message pairing)
# - error rate (chat_error)
# - escalation clicks (contact_host_click)
# - (keeps conversions + response times)
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

    if property_id is not None and pmc_id is not None:
        _assert_property_in_pmc(db, int(property_id), int(pmc_id))


        row = db.execute(
        text("""
        with base as (
            select *
            from analytics_events
            where ts >= :start
              and ts < :end
              and (:pmc_id is null or pmc_id = :pmc_id)
              and (:property_id is null or property_id = :property_id)
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
              and response_seconds <= 1800
        )
        select
            -- sessions
            count(*) filter (where event_name = 'chat_session_created') as sessions_total,

            -- messages
            count(*) filter (
                where event_name = 'message_sent'
                  and coalesce(data->>'sender', data->>'role') in ('user', 'guest')
            ) as user_messages,

            count(*) filter (
                where event_name = 'message_sent'
                  and coalesce(data->>'sender', data->>'role') in ('assistant', 'bot')
            ) as assistant_messages,

            -- response rate (pairing)
            (select count(*) from user_msgs) as user_message_samples,
            (select count(*) from response_clean) as responded_user_messages,
            (
              (select count(*) from response_clean)::float
              / nullif((select count(*) from user_msgs)::float, 0)
            ) as response_rate,

            -- followups funnel
            count(*) filter (where event_name = :followups_shown_event) as followups_shown,
            count(*) filter (where event_name = :followup_click_event) as followup_clicks,
            (
              count(*) filter (where event_name = :followup_click_event)::float
              / nullif(count(*) filter (where event_name = :followups_shown_event)::float, 0)
            ) as followup_conversion_rate,

            -- upgrades funnel (kept for later)
            count(*) filter (where event_name = ANY(:upgrade_start_events)) as upgrade_checkouts_started,
            count(*) filter (where event_name = ANY(:upgrade_purchase_events)) as upgrade_purchases,
            (
              count(*) filter (where event_name = ANY(:upgrade_purchase_events))::float
              / nullif(count(*) filter (where event_name = ANY(:upgrade_start_events))::float, 0)
            ) as upgrade_conversion_rate,

            -- reactions
            count(*) filter (where event_name = 'reaction_set' and data->>'value' = 'up') as reactions_up,
            count(*) filter (where event_name = 'reaction_set' and data->>'value' = 'down') as reactions_down,

            -- errors + escalation
            count(*) filter (where event_name = :chat_error_event) as chat_errors,
            count(*) filter (where event_name = :contact_host_click_event) as contact_host_clicks,

            (
              count(*) filter (where event_name = :chat_error_event)::float
              / nullif(count(*) filter (where event_name = 'message_sent'
                   and coalesce(data->>'sender', data->>'role') in ('user','guest')
                 )::float, 0)
            ) as error_rate_per_user_message,

            -- response time (seconds)
            (select avg(response_seconds) from response_clean) as avg_response_seconds,
            (select percentile_cont(0.5) within group (order by response_seconds) from response_clean) as p50_response_seconds
        from base;
        """).bindparams(
            bindparam("upgrade_start_events", type_=ARRAY(TEXT)),
            bindparam("upgrade_purchase_events", type_=ARRAY(TEXT)),
        ),
        {
            "start": start,
            "end": end,
            "pmc_id": pmc_id,
            "property_id": property_id,
            "followups_shown_event": FOLLOWUPS_SHOWN_EVENT,
            "followup_click_event": FOLLOWUP_CLICK_EVENT,
            "chat_error_event": CHAT_ERROR_EVENT,
            "contact_host_click_event": CONTACT_HOST_CLICK_EVENT,
            "upgrade_start_events": list(UPGRADE_START_EVENTS),
            "upgrade_purchase_events": list(UPGRADE_PURCHASE_EVENTS),
        },
    ).mappings().first() or {}


    return {
        # volume
        "sessions_total": int(_num(row.get("sessions_total"), 0)),
        "user_messages": int(_num(row.get("user_messages"), 0)),
        "assistant_messages": int(_num(row.get("assistant_messages"), 0)),

        # response rate
        "user_message_samples": int(_num(row.get("user_message_samples"), 0)),
        "responded_user_messages": int(_num(row.get("responded_user_messages"), 0)),
        "response_rate": float(_num(row.get("response_rate"), 0.0)),

        # followups conversion
        "followups_shown": int(_num(row.get("followups_shown"), 0)),
        "followup_clicks": int(_num(row.get("followup_clicks"), 0)),
        "followup_conversion_rate": float(_num(row.get("followup_conversion_rate"), 0.0)),

        # upgrades conversion (will be 0 until you emit events)
        "upgrade_checkouts_started": int(_num(row.get("upgrade_checkouts_started"), 0)),
        "upgrade_purchases": int(_num(row.get("upgrade_purchases"), 0)),
        "upgrade_conversion_rate": float(_num(row.get("upgrade_conversion_rate"), 0.0)),

        # reactions
        "reactions_up": int(_num(row.get("reactions_up"), 0)),
        "reactions_down": int(_num(row.get("reactions_down"), 0)),

        # errors + escalation
        "chat_errors": int(_num(row.get("chat_errors"), 0)),
        "contact_host_clicks": int(_num(row.get("contact_host_clicks"), 0)),
        "error_rate_per_user_message": float(_num(row.get("error_rate_per_user_message"), 0.0)),

        # response time
        "avg_response_seconds": float(_num(row.get("avg_response_seconds"), 0.0)),
        "p50_response_seconds": float(_num(row.get("p50_response_seconds"), 0.0)),
    }


# --------------------------------------------------
# RESPONSE RATE (standalone, with thresholds)
# --------------------------------------------------
@router.get("/response-rate")
def response_rate(
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

    if property_id is not None and pmc_id is not None:
        _assert_property_in_pmc(db, int(property_id), int(pmc_id))


    row = db.execute(
        text("""
        with base as (
            select *
            from analytics_events
            where ts >= :start
              and ts < :end
              and (:pmc_id is null or pmc_id = :pmc_id)
              and (:property_id is null or property_id = :property_id)
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
            where a.assistant_ts is not null
        ),
        clean as (
            select *
            from pairs
            where response_seconds is not null
              and response_seconds >= 0
              and response_seconds <= 1800
        )
        select
            (select count(*) from user_msgs) as user_messages,
            (select count(*) from clean) as responded_messages,
            ((select count(*) from clean)::float / nullif((select count(*) from user_msgs)::float, 0)) as response_rate,

            -- speed buckets
            count(*) filter (where response_seconds <= 30) as responded_30s,
            count(*) filter (where response_seconds <= 60) as responded_60s,
            count(*) filter (where response_seconds <= 300) as responded_5m
        from clean;
        """),
        {"start": start, "end": end, "pmc_id": pmc_id, "property_id": property_id},
    ).mappings().first() or {}

    user_messages = int(_num(row.get("user_messages"), 0))
    responded = int(_num(row.get("responded_messages"), 0))

    def _rate(n: int) -> float:
        return float(n) / float(user_messages) if user_messages else 0.0

    return {
        "user_messages": user_messages,
        "responded_messages": responded,
        "response_rate": float(_num(row.get("response_rate"), 0.0)),
        "responded_30s": int(_num(row.get("responded_30s"), 0)),
        "responded_60s": int(_num(row.get("responded_60s"), 0)),
        "responded_5m": int(_num(row.get("responded_5m"), 0)),
        "rate_30s": _rate(int(_num(row.get("responded_30s"), 0))),
        "rate_60s": _rate(int(_num(row.get("responded_60s"), 0))),
        "rate_5m": _rate(int(_num(row.get("responded_5m"), 0))),
    }


# --------------------------------------------------
# TIME SERIES
# Adds:
# - chat_errors
# - contact_host_clicks
# (response_rate is best computed as an overall metric; bucketed pairing is tricky)
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

    if property_id is not None and pmc_id is not None:
        _assert_property_in_pmc(db, int(property_id), int(pmc_id))


    rows = db.execute(
        text(f"""
        with buckets as (
            select generate_series(
                date_trunc('{trunc}', :start),
                date_trunc('{trunc}', :end),
                interval '1 {trunc}'
            ) as bucket

        ),
        filtered as (
            select *
            from analytics_events
            where ts >= :start
              and ts < :end
              and (:pmc_id is null or pmc_id = :pmc_id)
              and (:property_id is null or property_id = :property_id)
        ),
        agg as (
            select
                date_trunc('{trunc}', ts) as bucket,
                count(*) filter (where event_name = 'chat_session_created') as sessions,
                count(*) filter (where event_name = 'message_sent') as messages,
                count(*) filter (where event_name = :followup_click_event) as followup_clicks,
                count(*) filter (where event_name = :followups_shown_event) as followups_shown,
                count(*) filter (where event_name = :chat_error_event) as chat_errors,
                count(*) filter (where event_name = :contact_host_click_event) as contact_host_clicks
            from filtered
            group by 1
        )
        select
            b.bucket,
            coalesce(a.sessions, 0) as sessions,
            coalesce(a.messages, 0) as messages,
            coalesce(a.followup_clicks, 0) as followup_clicks,
            coalesce(a.followups_shown, 0) as followups_shown,
            coalesce(a.chat_errors, 0) as chat_errors,
            coalesce(a.contact_host_clicks, 0) as contact_host_clicks
        from buckets b
        left join agg a using (bucket)
        order by b.bucket asc;
        """),
        {
            "start": start,
            "end": end,
            "pmc_id": pmc_id,
            "property_id": property_id,
            "followup_click_event": FOLLOWUP_CLICK_EVENT,
            "followups_shown_event": FOLLOWUPS_SHOWN_EVENT,
            "chat_error_event": CHAT_ERROR_EVENT,
            "contact_host_click_event": CONTACT_HOST_CLICK_EVENT,
        },
    ).mappings().all()

    labels = []
    sessions, messages = [], []
    followup_clicks, followups_shown = [], []
    chat_errors, contact_host_clicks = [], []

    for r in rows:
        dt = r["bucket"]
        labels.append(dt.strftime("%b %d") if bucket == "day" else dt.strftime("%b %d %H:%M"))
        sessions.append(int(r["sessions"]))
        messages.append(int(r["messages"]))
        followup_clicks.append(int(r["followup_clicks"]))
        followups_shown.append(int(r["followups_shown"]))
        chat_errors.append(int(r["chat_errors"]))
        contact_host_clicks.append(int(r["contact_host_clicks"]))

    return {
        "labels": labels,
        "series": {
            "sessions": sessions,
            "messages": messages,
            "followup_clicks": followup_clicks,
            "followups_shown": followups_shown,
            "chat_errors": chat_errors,
            "contact_host_clicks": contact_host_clicks,
        },
    }


# --------------------------------------------------
# 🔥 TOP PROPERTIES
# Adds:
# - chat_errors
# - contact_host_clicks
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
            and (:pmc_id is null or pmc_id = :pmc_id)
            and property_id is not null
        ),
        agg as (
          select
            property_id,

            count(*) filter (where event_name = 'chat_session_created') as sessions,
            count(*) filter (where event_name = 'message_sent') as messages,

            -- followups
            count(*) filter (where event_name = :followups_shown_event) as followups_shown,
            count(*) filter (where event_name = :followup_click_event) as followup_clicks,

            -- upgrades (kept for later)
            count(*) filter (where event_name = ANY(:upgrade_start_events)) as upgrade_checkouts_started,
            count(*) filter (where event_name = ANY(:upgrade_purchase_events)) as upgrade_purchases,

            -- errors + escalation
            count(*) filter (where event_name = :chat_error_event) as chat_errors,
            count(*) filter (where event_name = :contact_host_click_event) as contact_host_clicks

          from base
          group by 1
        )
        select
          property_id,
          sessions,
          messages,

          followups_shown,
          followup_clicks,
          (followup_clicks::float / nullif(followups_shown::float, 0)) as followup_conversion_rate,

          upgrade_checkouts_started,
          upgrade_purchases,
          (upgrade_purchases::float / nullif(upgrade_checkouts_started::float, 0)) as upgrade_conversion_rate,

          chat_errors,
          contact_host_clicks
        from agg
        order by sessions desc, messages desc
        limit :limit;
        """).bindparams(
            bindparam("upgrade_start_events", type_=ARRAY(TEXT)),
            bindparam("upgrade_purchase_events", type_=ARRAY(TEXT)),
        ),
        {
            "start": start,
            "end": end,
            "pmc_id": pmc_id,
            "limit": limit,
            "followups_shown_event": FOLLOWUPS_SHOWN_EVENT,
            "followup_click_event": FOLLOWUP_CLICK_EVENT,
            "chat_error_event": CHAT_ERROR_EVENT,
            "contact_host_click_event": CONTACT_HOST_CLICK_EVENT,
            "upgrade_start_events": list(UPGRADE_START_EVENTS),
            "upgrade_purchase_events": list(UPGRADE_PURCHASE_EVENTS),
        },
    ).mappings().all()


    return {"rows": [dict(r) for r in rows]}


# --------------------------------------------------
# 📊 CONVERSION RATE (both meanings, kept for later)
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

    if property_id is not None and pmc_id is not None:
        _assert_property_in_pmc(db, int(property_id), int(pmc_id))


        row = db.execute(
        text("""
        with base as (
          select *
          from analytics_events
          where ts >= :start and ts < :end
            and (:pmc_id is null or pmc_id = :pmc_id)
            and (:property_id is null or property_id = :property_id)
        )
        select
          -- followups funnel
          count(*) filter (where event_name = :followups_shown_event) as followups_shown,
          count(*) filter (where event_name = :followup_click_event) as followup_clicks,
          (
            count(*) filter (where event_name = :followup_click_event)::float
            / nullif(count(*) filter (where event_name = :followups_shown_event)::float, 0)
          ) as followup_conversion_rate,

          -- upgrades funnel (kept for later)
          count(*) filter (where event_name = ANY(:upgrade_start_events)) as upgrade_checkouts_started,
          count(*) filter (where event_name = ANY(:upgrade_purchase_events)) as upgrade_purchases,
          (
            count(*) filter (where event_name = ANY(:upgrade_purchase_events))::float
            / nullif(count(*) filter (where event_name = ANY(:upgrade_start_events))::float, 0)
          ) as upgrade_conversion_rate
        from base;
        """).bindparams(
            bindparam("upgrade_start_events", type_=ARRAY(TEXT)),
            bindparam("upgrade_purchase_events", type_=ARRAY(TEXT)),
        ),
        {
            "start": start,
            "end": end,
            "pmc_id": pmc_id,
            "property_id": property_id,
            "followups_shown_event": FOLLOWUPS_SHOWN_EVENT,
            "followup_click_event": FOLLOWUP_CLICK_EVENT,
            "upgrade_start_events": list(UPGRADE_START_EVENTS),
            "upgrade_purchase_events": list(UPGRADE_PURCHASE_EVENTS),
        },
    ).mappings().first() or {}


    return {
        "followups_shown": int(row.get("followups_shown") or 0),
        "followup_clicks": int(row.get("followup_clicks") or 0),
        "followup_conversion_rate": float(row.get("followup_conversion_rate") or 0.0),

        "upgrade_checkouts_started": int(row.get("upgrade_checkouts_started") or 0),
        "upgrade_purchases": int(row.get("upgrade_purchases") or 0),
        "upgrade_conversion_rate": float(row.get("upgrade_conversion_rate") or 0.0),
    }


# --------------------------------------------------
# ⏱ AVG RESPONSE TIME (kept)
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

    if property_id is not None and pmc_id is not None:
        _assert_property_in_pmc(db, int(property_id), int(pmc_id))


    row = db.execute(
        text("""
        with base as (
          select *
          from analytics_events
          where ts >= :start and ts < :end
            and (:pmc_id is null or pmc_id = :pmc_id)
            and (:property_id is null or property_id = :property_id)
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
# Adds:
# - chat_errors, contact_host_clicks (if those events include data.assistant/data.variant)
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

    if property_id is not None and pmc_id is not None:
        _assert_property_in_pmc(db, int(property_id), int(pmc_id))


        rows = db.execute(
        text("""
        with base as (
          select
            *,
            coalesce(data->>'assistant', data->>'variant', 'default') as assistant_key
          from analytics_events
          where ts >= :start and ts < :end
            and (:pmc_id is null or pmc_id = :pmc_id)
            and (:property_id is null or property_id = :property_id)
        ),
        msg_base as (
          select
            ts,
            session_id,
            coalesce(data->>'sender', data->>'role') as sender,
            assistant_key
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
        ),
        funnels as (
          select
            assistant_key,

            -- followups
            count(*) filter (where event_name = :followups_shown_event) as followups_shown,
            count(*) filter (where event_name = :followup_click_event) as followup_clicks,
            (
              count(*) filter (where event_name = :followup_click_event)::float
              / nullif(count(*) filter (where event_name = :followups_shown_event)::float, 0)
            ) as followup_conversion_rate,

            -- upgrades (kept for later)
            count(*) filter (where event_name = ANY(:upgrade_start_events)) as upgrade_checkouts_started,
            count(*) filter (where event_name = ANY(:upgrade_purchase_events)) as upgrade_purchases,
            (
              count(*) filter (where event_name = ANY(:upgrade_purchase_events))::float
              / nullif(count(*) filter (where event_name = ANY(:upgrade_start_events))::float, 0)
            ) as upgrade_conversion_rate,

            -- errors + escalation
            count(*) filter (where event_name = :chat_error_event) as chat_errors,
            count(*) filter (where event_name = :contact_host_click_event) as contact_host_clicks

          from base
          group by 1
        )
        select
          coalesce(m.assistant_key, r.assistant_key, f.assistant_key) as assistant_key,

          coalesce(m.user_messages, 0) as user_messages,
          coalesce(m.assistant_messages, 0) as assistant_messages,

          coalesce(r.response_samples, 0) as response_samples,
          coalesce(r.avg_seconds, 0) as avg_response_seconds,
          coalesce(r.p50_seconds, 0) as p50_response_seconds,

          coalesce(f.followups_shown, 0) as followups_shown,
          coalesce(f.followup_clicks, 0) as followup_clicks,
          coalesce(f.followup_conversion_rate, 0) as followup_conversion_rate,

          coalesce(f.upgrade_checkouts_started, 0) as upgrade_checkouts_started,
          coalesce(f.upgrade_purchases, 0) as upgrade_purchases,
          coalesce(f.upgrade_conversion_rate, 0) as upgrade_conversion_rate,

          coalesce(f.chat_errors, 0) as chat_errors,
          coalesce(f.contact_host_clicks, 0) as contact_host_clicks

        from msgs m
        full join rt r using (assistant_key)
        full join funnels f using (assistant_key)
        order by assistant_messages desc, response_samples desc
        limit :limit;
        """).bindparams(
            bindparam("upgrade_start_events", type_=ARRAY(TEXT)),
            bindparam("upgrade_purchase_events", type_=ARRAY(TEXT)),
        ),
        {
            "start": start,
            "end": end,
            "pmc_id": pmc_id,
            "property_id": property_id,
            "limit": limit,
            "followups_shown_event": FOLLOWUPS_SHOWN_EVENT,
            "followup_click_event": FOLLOWUP_CLICK_EVENT,
            "chat_error_event": CHAT_ERROR_EVENT,
            "contact_host_click_event": CONTACT_HOST_CLICK_EVENT,
            "upgrade_start_events": list(UPGRADE_START_EVENTS),
            "upgrade_purchase_events": list(UPGRADE_PURCHASE_EVENTS),
        },
    ).mappings().all()


    return {"rows": [dict(r) for r in rows]}
