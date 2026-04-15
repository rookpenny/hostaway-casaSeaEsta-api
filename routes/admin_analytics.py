#admin_analytics.py

from datetime import datetime, timezone, timedelta
from typing import Optional, Literal
from fastapi import APIRouter, Depends, Query, Request, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text, bindparam
from sqlalchemy.dialects.postgresql import ARRAY, TEXT as PG_TEXT, BIGINT

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





def _enforce_scope(request: Request, pmc_id: int | None) -> int | None:
    role = (request.session.get("role") or "").lower()

    # PMC users: FORCE scope to their session pmc_id, ignore query param
    if role == "pmc":
        session_pmc_id = request.session.get("pmc_id")
        if not session_pmc_id:
            raise HTTPException(status_code=403, detail="PMC scope missing in session")
        return int(session_pmc_id)

    # Super users: allow optional pmc_id filter
    if role == "super":
        return int(pmc_id) if pmc_id is not None else None

    raise HTTPException(status_code=401, detail="Not authenticated")


def _num(x, default=0):
    return default if x is None else x


def _assert_property_in_pmc(db: Session, property_id: int, pmc_id: int) -> None:
    stmt = text("""
        select 1
        from properties
        where id = :pid
          and pmc_id = :pmc
        limit 1
    """).bindparams(
        bindparam("pid", type_=BIGINT),
        bindparam("pmc", type_=BIGINT),
    )
    ok = db.execute(stmt, {"pid": int(property_id), "pmc": int(pmc_id)}).first()
    if not ok:
        raise HTTPException(status_code=403, detail="Forbidden property scope")


def _with_upgrade_array_binds(stmt_text: str):
    """
    Ensures ANY(:upgrade_*_events) binds are sent as Postgres text[] instead of an untyped Python list.
    This avoids 500s like "operator does not exist" / "can't adapt type" / etc.
    """
    return text(stmt_text).bindparams(
        bindparam("start"),
        bindparam("end"),
        bindparam("pmc_id", type_=BIGINT),
        bindparam("property_id", type_=BIGINT),
        bindparam("upgrade_start_events", type_=ARRAY(PG_TEXT)),
        bindparam("upgrade_purchase_events", type_=ARRAY(PG_TEXT)),
    )


# --------------------------------------------------
# SUMMARY
# --------------------------------------------------

@router.get("/summary")
def summary(
    request: Request,
    from_ms: int | None = Query(default=None, alias="from"),
    to_ms: int | None = Query(default=None, alias="to"),
    days: int = Query(30, ge=1, le=365),
    property_id: Optional[int] = None,
    pmc_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    pmc_id = _enforce_scope(request, pmc_id)

    if from_ms is not None and to_ms is not None:
        start = ms_to_dt(from_ms)
        end = ms_to_dt(to_ms)
    else:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=int(days))

    if property_id is not None and pmc_id is not None:
        _assert_property_in_pmc(db, int(property_id), int(pmc_id))

    stmt = text("""
        with filtered_msgs as (
            select
                cm.id,
                cm.session_id,
                cm.sender,
                cm.category,
                cm.created_at
            from chat_messages cm
            join chat_sessions cs on cs.id = cm.session_id
            join properties p on p.id = cs.property_id
            where cm.created_at >= :start
              and cm.created_at < :end
              and (:pmc_id is null or p.pmc_id = :pmc_id)
              and (:property_id is null or cs.property_id = :property_id)
        ),
        active_sessions as (
            select distinct session_id
            from filtered_msgs
            where session_id is not null
        )
        select
            (select count(*) from active_sessions) as sessions_total,
            (
              select count(distinct session_id)
              from filtered_msgs
              where sender != 'guest'
            ) as responded_sessions,
            (
              select count(*)
              from filtered_msgs
              where category = 'urgent'
            ) as followup_clicks,
            (
              select count(*)
              from filtered_msgs
              where category = 'error'
            ) as chat_errors
    """).bindparams(
        bindparam("pmc_id", type_=BIGINT),
        bindparam("property_id", type_=BIGINT),
    )

    row = db.execute(
        stmt,
        {
            "start": start,
            "end": end,
            "pmc_id": pmc_id,
            "property_id": property_id,
        },
    ).mappings().first() or {}

    sessions_total = int(_num(row.get("sessions_total"), 0))
    responded_sessions = int(_num(row.get("responded_sessions"), 0))

    response_rate = (responded_sessions / sessions_total * 100.0) if sessions_total else 0.0

    return {
        "window_days": int(days),
        "sessions_total": sessions_total,
        "response_rate": round(float(response_rate), 1),
        "followup_clicks": int(_num(row.get("followup_clicks"), 0)),
        "chat_errors": int(_num(row.get("chat_errors"), 0)),
    }

# --------------------------------------------------
# RESPONSE RATE
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

    stmt = text("""
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
            count(*) filter (where response_seconds <= 30) as responded_30s,
            count(*) filter (where response_seconds <= 60) as responded_60s,
            count(*) filter (where response_seconds <= 300) as responded_5m
        from clean;
    """).bindparams(
        bindparam("pmc_id", type_=BIGINT),
        bindparam("property_id", type_=BIGINT),
    )

    try:
        row = db.execute(
            stmt,
            {"start": start, "end": end, "pmc_id": pmc_id, "property_id": property_id},
        ).mappings().first() or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"response-rate query failed: {e}")

    user_messages = int(_num(row.get("user_messages"), 0))

    def _rate(n: int) -> float:
        return float(n) / float(user_messages) if user_messages else 0.0

    return {
        "user_messages": user_messages,
        "responded_messages": int(_num(row.get("responded_messages"), 0)),
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
# --------------------------------------------------
@router.get("/timeseries")
def timeseries(
    request: Request,
    from_ms: int | None = Query(default=None, alias="from"),
    to_ms: int | None = Query(default=None, alias="to"),
    days: int = Query(30, ge=1, le=365),
    property_id: Optional[int] = None,
    pmc_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    pmc_id = _enforce_scope(request, pmc_id)

    if from_ms is not None and to_ms is not None:
        start = ms_to_dt(from_ms)
        end = ms_to_dt(to_ms)
        window_days = max(1, (end.date() - start.date()).days)
        prev_start = start - (end - start)
        prev_end = start
    else:
        now = datetime.now(timezone.utc)
    
        # 🔑 Find start of current week (Monday)
        start_of_week = now - timedelta(days=now.weekday())
    
        # 🔑 End of week (Sunday 23:59)
        end_of_week = start_of_week + timedelta(days=6, hours=23, minutes=59, seconds=59)
    
        # 🔑 Anchor chart to end of current week
        end = end_of_week
    
        # 🔑 Go backwards 30 days from that anchor
        start = end - timedelta(days=int(days) - 1)

        # ✅ ADD IT RIGHT HERE
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
    
        window_days = int(days)
    
        prev_start = start - timedelta(days=int(days))
        prev_end = start

    if property_id is not None and pmc_id is not None:
        _assert_property_in_pmc(db, int(property_id), int(pmc_id))

    day_stmt = text("""
        with filtered_msgs as (
            select
                cm.id,
                cm.session_id,
                cm.sender,
                cm.category,
                cm.sentiment,
                cm.created_at,
                cs.property_id
            from chat_messages cm
            join chat_sessions cs on cs.id = cm.session_id
            join properties p on p.id = cs.property_id
            where cm.created_at >= :start
              and cm.created_at < :end
              and (:pmc_id is null or p.pmc_id = :pmc_id)
              and (:property_id is null or cs.property_id = :property_id)
        ),
        last_msg_per_session as (
            select distinct on (session_id)
                session_id,
                property_id,
                created_at,
                category,
                sentiment
            from filtered_msgs
            order by session_id, created_at desc
        ),
        responded_sessions as (
            select distinct session_id
            from filtered_msgs
            where sender != 'guest'
        ),
        daily_msgs as (
            select
                date_trunc('day', created_at) as bucket,
                count(*) as messages,
        
                count(*) filter (where sender in ('guest','user')) as messages_user,
                count(*) filter (where sender in ('assistant','bot')) as messages_assistant,
        
                count(*) filter (where category = 'error') as errors
            from filtered_msgs
            group by 1
        ),
        daily_sessions as (
            select
                date_trunc('day', lm.created_at) as bucket,
                count(*) as chats,
                count(*) filter (
                    where lm.category = 'urgent'
                       or lower(coalesce(lm.sentiment, '')) = 'negative'
                ) as lost_opportunity,
                count(*) filter (
                    where exists (
                        select 1 from responded_sessions rs where rs.session_id = lm.session_id
                    )
                ) as responded
            from last_msg_per_session lm
            group by 1
        )
        select
            coalesce(ds.bucket, dm.bucket) as bucket,
            coalesce(ds.chats, 0) as chats,
            coalesce(ds.lost_opportunity, 0) as lost_opportunity,
            coalesce(ds.responded, 0) as responded,
            coalesce(dm.messages, 0) as messages,
            coalesce(dm.messages_user, 0) as messages_user,
            coalesce(dm.messages_assistant, 0) as messages_assistant,
            coalesce(dm.errors, 0) as errors
        from daily_sessions ds
        full outer join daily_msgs dm on dm.bucket = ds.bucket
        order by bucket asc
    """).bindparams(
        bindparam("pmc_id", type_=BIGINT),
        bindparam("property_id", type_=BIGINT),
    )

    prev_stmt = text("""
        with filtered_msgs as (
            select
                cm.id,
                cm.session_id,
                cm.sender,
                cm.category,
                cm.sentiment,
                cm.created_at,
                cs.property_id
            from chat_messages cm
            join chat_sessions cs on cs.id = cm.session_id
            join properties p on p.id = cs.property_id
            where cm.created_at >= :start
              and cm.created_at < :end
              and (:pmc_id is null or p.pmc_id = :pmc_id)
              and (:property_id is null or cs.property_id = :property_id)
        ),
        last_msg_per_session as (
            select distinct on (session_id)
                session_id,
                property_id,
                created_at,
                category,
                sentiment
            from filtered_msgs
            order by session_id, created_at desc
        ),
        responded_sessions as (
            select distinct session_id
            from filtered_msgs
            where sender != 'guest'
        )
        select
            date_trunc('day', lm.created_at) as bucket,
            count(*) as chats,
            count(*) filter (
                where lm.category = 'urgent'
                   or lower(coalesce(lm.sentiment, '')) = 'negative'
            ) as lost_opportunity,
            count(*) filter (
                where exists (
                    select 1 from responded_sessions rs where rs.session_id = lm.session_id
                )
            ) as responded
        from last_msg_per_session lm
        group by 1
        order by bucket asc
    """).bindparams(
        bindparam("pmc_id", type_=BIGINT),
        bindparam("property_id", type_=BIGINT),
    )

    hour_stmt = text("""
        with filtered_msgs as (
            select
                cm.created_at
            from chat_messages cm
            join chat_sessions cs on cs.id = cm.session_id
            join properties p on p.id = cs.property_id
            where cm.created_at >= :start
              and cm.created_at < :end
              and (:pmc_id is null or p.pmc_id = :pmc_id)
              and (:property_id is null or cs.property_id = :property_id)
        )
        select
            extract(hour from created_at) as hr,
            count(*) as value
        from filtered_msgs
        group by 1
        order by 1
    """).bindparams(
        bindparam("pmc_id", type_=BIGINT),
        bindparam("property_id", type_=BIGINT),
    )

    emotion_stmt = text("""
        with scoped_sessions as (
            select
                cs.id,
                cs.guest_mood,
                cs.emotional_signals
            from chat_sessions cs
            join properties p on p.id = cs.property_id
            where (:pmc_id is null or p.pmc_id = :pmc_id)
              and (:property_id is null or cs.property_id = :property_id)
              and exists (
                  select 1
                  from chat_messages cm
                  where cm.session_id = cs.id
                    and cm.created_at >= :start
                    and cm.created_at < :end
              )
        )
        select
            guest_mood,
            emotional_signals
        from scoped_sessions
    """).bindparams(
        bindparam("pmc_id", type_=BIGINT),
        bindparam("property_id", type_=BIGINT),
    )

    lifecycle_stmt = text("""
        with scoped_sessions as (
            select
                cs.id,
                cs.arrival_date,
                cs.departure_date
            from chat_sessions cs
            join properties p on p.id = cs.property_id
            where (:pmc_id is null or p.pmc_id = :pmc_id)
              and (:property_id is null or cs.property_id = :property_id)
              and exists (
                  select 1
                  from chat_messages cm
                  where cm.session_id = cs.id
                    and cm.created_at >= :start
                    and cm.created_at < :end
              )
        )
        select
            arrival_date,
            departure_date
        from scoped_sessions
    """).bindparams(
        bindparam("pmc_id", type_=BIGINT),
        bindparam("property_id", type_=BIGINT),
    )

    cur_rows = db.execute(
        day_stmt,
        {"start": start, "end": end, "pmc_id": pmc_id, "property_id": property_id},
    ).mappings().all()

    prev_rows = db.execute(
        prev_stmt,
        {"start": prev_start, "end": prev_end, "pmc_id": pmc_id, "property_id": property_id},
    ).mappings().all()

    hour_rows = db.execute(
        hour_stmt,
        {"start": start, "end": end, "pmc_id": pmc_id, "property_id": property_id},
    ).mappings().all()

    emotion_rows = db.execute(
        emotion_stmt,
        {"start": start, "end": end, "pmc_id": pmc_id, "property_id": property_id},
    ).mappings().all()

    lifecycle_rows = db.execute(
        lifecycle_stmt,
        {"start": start, "end": end, "pmc_id": pmc_id, "property_id": property_id},
    ).mappings().all()

    cur_by_day = {}
    for r in cur_rows:
        bucket = r["bucket"].date().isoformat()
        chats = int(_num(r.get("chats"), 0))
        responded = int(_num(r.get("responded"), 0))
        cur_by_day[bucket] = {
            "chats": chats,
            "conversion": round((responded / chats) * 100) if chats else 0,
            "lost_opportunity": int(_num(r.get("lost_opportunity"), 0)),
            "messages": int(_num(r.get("messages"), 0)),
        
            "messages_user": int(_num(r.get("messages_user"), 0)),
            "messages_assistant": int(_num(r.get("messages_assistant"), 0)),
        
            "errors": int(_num(r.get("errors"), 0)),
        }

    prev_by_day = {}
    for r in prev_rows:
        bucket = r["bucket"].date()
        shifted = (bucket + timedelta(days=window_days)).isoformat()
        chats = int(_num(r.get("chats"), 0))
        responded = int(_num(r.get("responded"), 0))
        prev_by_day[shifted] = {
            "chats": chats,
            "conversion": round((responded / chats) * 100) if chats else 0,
            "lost_opportunity": int(_num(r.get("lost_opportunity"), 0)),
        }

    items = []
    max_chats = 0
    max_conv = 0
    max_lost = 0

    for i in range(window_days):
        d = (start.date() + timedelta(days=i))
        key = d.isoformat()

        cur = cur_by_day.get(key, {
            "chats": 0,
            "conversion": 0,
            "lost_opportunity": 0,
            "messages": 0,
            "errors": 0,
        })
        prev = prev_by_day.get(key, {
            "chats": 0,
            "conversion": 0,
            "lost_opportunity": 0,
        })

        delta = 0
        if prev["chats"] > 0:
            delta = round(((cur["chats"] - prev["chats"]) / prev["chats"]) * 100)

        max_chats = max(max_chats, cur["chats"])
        max_conv = max(max_conv, cur["conversion"])
        max_lost = max(max_lost, cur["lost_opportunity"])
        items.append({
            "date": key,
            "label": d.strftime("%b %-d"),
            "day": d.strftime("%a"),
            "chats": cur["chats"],
            "conversion": cur["conversion"],
            "lost_opportunity": cur["lost_opportunity"],
            "messages": cur["messages"],
        
            "messages_user": cur.get("messages_user", 0),
            "messages_assistant": cur.get("messages_assistant", 0),
        
            "errors": cur["errors"],
            "delta": delta,
            "event": "stable",
            "previous": prev,
        })

    avg_chats = round(sum(x["chats"] for x in items) / len(items)) if items else 0

    for item in items:
        if item["chats"] == max_chats and item["chats"] > 0:
            item["event"] = "peak"
        elif item["lost_opportunity"] == max_lost and item["lost_opportunity"] > 0:
            item["event"] = "friction"
        elif item["conversion"] == max_conv and item["conversion"] > 0:
            item["event"] = "convert"
        elif item["chats"] > avg_chats * 1.15:
            item["event"] = "inquiry"
        elif item["chats"] < max(1, avg_chats * 0.6):
            item["event"] = "quiet"

    hour_map = {int(r["hr"]): int(_num(r.get("value"), 0)) for r in hour_rows}
    hour_labels = [
        ("12a", 0), ("3a", 3), ("6a", 6), ("9a", 9),
        ("12p", 12), ("3p", 15), ("6p", 18), ("9p", 21),
    ]
    hour_items = [{"label": label, "value": int(hour_map.get(hour, 0))} for label, hour in hour_labels]
    peak_hour = max(hour_items, key=lambda x: x["value"]) if hour_items else {"label": "—", "value": 0}
    peak_window = {
        "12a": "12 AM–3 AM",
        "3a": "3 AM–6 AM",
        "6a": "6 AM–9 AM",
        "9a": "9 AM–12 PM",
        "12p": "12 PM–3 PM",
        "3p": "3 PM–6 PM",
        "6p": "6 PM–9 PM",
        "9p": "9 PM–12 AM",
    }.get(peak_hour["label"], "—")

    emotion_counts = {
        "calm": 0,
        "confused": 0,
        "worried": 0,
        "upset": 0,
        "panicked": 0,
        "angry": 0,
        "stressed": 0,
    }

    for r in emotion_rows:
        guest_mood = (r.get("guest_mood") or "").strip().lower()
        if guest_mood:
            emotion_counts[guest_mood] = emotion_counts.get(guest_mood, 0) + 1

        signals = r.get("emotional_signals") or []
        if isinstance(signals, list):
            for sig in signals:
                sig_key = str(sig or "").strip().lower()
                if sig_key:
                    emotion_counts[sig_key] = emotion_counts.get(sig_key, 0) + 1

    emotion_total = sum(emotion_counts.values()) or 1
    emotion_items = [
        {"label": "Calm", "value": round((emotion_counts.get("calm", 0) / emotion_total) * 100), "tone": "emerald"},
        {"label": "Confused", "value": round((emotion_counts.get("confused", 0) / emotion_total) * 100), "tone": "blue"},
        {"label": "Worried", "value": round((emotion_counts.get("worried", 0) / emotion_total) * 100), "tone": "indigo"},
        {"label": "Upset", "value": round((emotion_counts.get("upset", 0) / emotion_total) * 100), "tone": "amber"},
        {"label": "Panicked", "value": round((emotion_counts.get("panicked", 0) / emotion_total) * 100), "tone": "rose"},
        {"label": "Angry", "value": round((emotion_counts.get("angry", 0) / emotion_total) * 100), "tone": "rose"},
        {"label": "Stressed", "value": round((emotion_counts.get("stressed", 0) / emotion_total) * 100), "tone": "orange"},
    ]

    strongest_emotion = max(emotion_items, key=lambda x: x["value"]) if emotion_items else None
    emotion_spike = {
        "title": (
            f"{strongest_emotion['label']} is the strongest emotional signal right now"
            if strongest_emotion and strongest_emotion["value"] > 0
            else "No major emotional spike detected"
        ),
        "body": (
            f"{strongest_emotion['value']}% of current emotional signals cluster around {strongest_emotion['label'].lower()} conversations."
            if strongest_emotion and strongest_emotion["value"] > 0
            else "Current conversations are relatively balanced with no major emotional concentration."
        ),
    }

    today = datetime.now(timezone.utc).date()
    lifecycle_counts = {"inquiry": 0, "upcoming": 0, "current": 0, "checked_out": 0}
    for r in lifecycle_rows:
        arrival = r.get("arrival_date")
        departure = r.get("departure_date")

        try:
            if arrival and departure:
                a = datetime.fromisoformat(str(arrival)).date()
                d = datetime.fromisoformat(str(departure)).date()
                if today < a:
                    lifecycle_counts["upcoming"] += 1
                elif a <= today <= d:
                    lifecycle_counts["current"] += 1
                else:
                    lifecycle_counts["checked_out"] += 1
            else:
                lifecycle_counts["inquiry"] += 1
        except Exception:
            lifecycle_counts["inquiry"] += 1

    total_lifecycle = sum(lifecycle_counts.values()) or 1
    lifecycle_pct = {
        "total": total_lifecycle,
        "inquiry": round((lifecycle_counts["inquiry"] / total_lifecycle) * 100),
        "upcoming": round((lifecycle_counts["upcoming"] / total_lifecycle) * 100),
        "current": round((lifecycle_counts["current"] / total_lifecycle) * 100),
        "checked_out": round((lifecycle_counts["checked_out"] / total_lifecycle) * 100),
    }

    return {
        "window_days": int(days),
        "days": items,
        "lifecycle": lifecycle_pct,
        "hours": {
            "peak_window": peak_window,
            "items": hour_items,
        },
        "emotions": {
            "items": emotion_items,
        },
        "emotion_spike": emotion_spike,
    }


# --------------------------------------------------
# TOP PROPERTIES
# --------------------------------------------------
@router.get("/top-properties")
def top_properties(
    request: Request,
    from_ms: int | None = Query(default=None, alias="from"),
    to_ms: int | None = Query(default=None, alias="to"),
    days: int = Query(30, ge=1, le=365),
    pmc_id: Optional[int] = None,
    property_id: Optional[int] = None,
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    pmc_id = _enforce_scope(request, pmc_id)

    if from_ms is not None and to_ms is not None:
        start = ms_to_dt(from_ms)
        end = ms_to_dt(to_ms)
    else:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=int(days))

    if property_id is not None and pmc_id is not None:
        _assert_property_in_pmc(db, int(property_id), int(pmc_id))

    stmt = text("""
        with filtered_msgs as (
            select
                cm.id,
                cm.session_id,
                cm.sender,
                cm.category,
                cm.sentiment,
                cm.created_at,
                cs.property_id
            from chat_messages cm
            join chat_sessions cs on cs.id = cm.session_id
            join properties p on p.id = cs.property_id
            where cm.created_at >= :start
              and cm.created_at < :end
              and (:pmc_id is null or p.pmc_id = :pmc_id)
              and (:property_id is null or cs.property_id = :property_id)
        ),
        per_property as (
            select
                property_id,
                count(distinct session_id) as sessions,
                count(*) as messages,
                count(distinct session_id) filter (where sender != 'guest') as responded_sessions,
                count(*) filter (where category = 'error') as chat_errors,
                count(distinct session_id) filter (
                    where category = 'urgent'
                       or lower(coalesce(sentiment, '')) = 'negative'
                ) as escalations
            from filtered_msgs
            group by property_id
        )
        select
            pp.property_id,
            p.property_name,
            pp.sessions,
            pp.messages,
            pp.responded_sessions,
            pp.chat_errors,
            pp.escalations
        from per_property pp
        join properties p on p.id = pp.property_id
        order by pp.messages desc, pp.sessions desc, pp.escalations desc
        limit :limit
    """).bindparams(
        bindparam("pmc_id", type_=BIGINT),
        bindparam("property_id", type_=BIGINT),
        bindparam("limit", type_=BIGINT),
    )

    rows = db.execute(
        stmt,
        {
            "start": start,
            "end": end,
            "pmc_id": pmc_id,
            "property_id": property_id,
            "limit": int(limit),
        },
    ).mappings().all()

    items = []
    for r in rows:
        sessions = int(_num(r.get("sessions"), 0))
        responded = int(_num(r.get("responded_sessions"), 0))
        conversion_rate = (responded / sessions * 100.0) if sessions else 0.0

        items.append({
            "property_id": int(r["property_id"]),
            "property_name": r.get("property_name") or "Unknown",
            "sessions": sessions,
            "messages": int(_num(r.get("messages"), 0)),
            "followup_conversion_rate": round(float(conversion_rate), 1),
            "chat_errors": int(_num(r.get("chat_errors"), 0)),
            "escalations": int(_num(r.get("escalations"), 0)),
        })

    return {
        "window_days": int(days),
        "items": items,
    }

# --------------------------------------------------
# CONVERSION
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

    stmt = _with_upgrade_array_binds("""
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

          -- upgrades funnel (typed arrays)
          count(*) filter (where event_name = ANY(:upgrade_start_events)) as upgrade_checkouts_started,
          count(*) filter (where event_name = ANY(:upgrade_purchase_events)) as upgrade_purchases,
          (
            count(*) filter (where event_name = ANY(:upgrade_purchase_events))::float
            / nullif(count(*) filter (where event_name = ANY(:upgrade_start_events))::float, 0)
          ) as upgrade_conversion_rate
        from base;
    """).bindparams(
        bindparam("followups_shown_event", type_=PG_TEXT),
        bindparam("followup_click_event", type_=PG_TEXT),
    )

    try:
        row = db.execute(
            stmt,
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"conversion query failed: {e}")

    return {
        "followups_shown": int(row.get("followups_shown") or 0),
        "followup_clicks": int(row.get("followup_clicks") or 0),
        "followup_conversion_rate": float(row.get("followup_conversion_rate") or 0.0),
        "upgrade_checkouts_started": int(row.get("upgrade_checkouts_started") or 0),
        "upgrade_purchases": int(row.get("upgrade_purchases") or 0),
        "upgrade_conversion_rate": float(row.get("upgrade_conversion_rate") or 0.0),
    }


# --------------------------------------------------
# RESPONSE TIME
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

    stmt = text("""
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
    """).bindparams(
        bindparam("pmc_id", type_=BIGINT),
        bindparam("property_id", type_=BIGINT),
    )

    try:
        row = db.execute(
            stmt,
            {"start": start, "end": end, "pmc_id": pmc_id, "property_id": property_id},
        ).mappings().first() or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"response-time query failed: {e}")

    return {
        "samples": int(row.get("samples") or 0),
        "avg_seconds": float(row.get("avg_seconds") or 0.0),
        "p50_seconds": float(row.get("p50_seconds") or 0.0),
        "p90_seconds": float(row.get("p90_seconds") or 0.0),
    }


# --------------------------------------------------
# ASSISTANT PERFORMANCE
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

    stmt = _with_upgrade_array_binds("""
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

            -- upgrades (typed arrays)
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
        bindparam("limit", type_=BIGINT),
        bindparam("followups_shown_event", type_=PG_TEXT),
        bindparam("followup_click_event", type_=PG_TEXT),
        bindparam("chat_error_event", type_=PG_TEXT),
        bindparam("contact_host_click_event", type_=PG_TEXT),
    )

    try:
        rows = db.execute(
            stmt,
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"assistant-performance query failed: {e}")

    return {"rows": [dict(r) for r in rows]}


@router.get("/mood-current")
def mood_current(
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

    # "Current mood" = latest GUEST message per session, within time window
    stmt = text("""
        with scoped_sessions as (
            select cs.id as session_id, cs.property_id
            from chat_sessions cs
            join properties p on p.id = cs.property_id
            where (:pmc_id is null or p.pmc_id = :pmc_id)
              and (:property_id is null or cs.property_id = :property_id)
        ),
        guest_msgs as (
            select
                cm.session_id,
                ss.property_id,
                cm.created_at,
                coalesce(cm.sentiment, 'neutral') as sentiment,
                coalesce(cm.sentiment_data->>'mood', 'calm') as mood,
                row_number() over (
                    partition by cm.session_id
                    order by cm.created_at desc
                ) as rn
            from chat_messages cm
            join scoped_sessions ss on ss.session_id = cm.session_id
            where cm.sender in ('guest', 'user')
              and cm.created_at >= :start
              and cm.created_at < :end
        ),
        latest as (
            select *
            from guest_msgs
            where rn = 1
        )
        select
            mood,
            count(*) as sessions
        from latest
        group by 1
        order by sessions desc;
    """).bindparams(
        bindparam("pmc_id", type_=BIGINT),
        bindparam("property_id", type_=BIGINT),
    )

    try:
        rows = db.execute(
            stmt,
            {"start": start, "end": end, "pmc_id": pmc_id, "property_id": property_id},
        ).mappings().all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"mood-current query failed: {e}")

    return {"rows": [dict(r) for r in rows]}

