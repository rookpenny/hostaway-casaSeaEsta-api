# routes/admin_messages.py
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import PMCMessage

router = APIRouter(prefix="/admin/messages", tags=["admin-messages"])


@router.get("/unread-count")
def unread_count(
    pmc_id: int = Query(..., description="PMC id"),
    db: Session = Depends(get_db),
):
    count = (
        db.query(func.count(PMCMessage.id))
        .filter(PMCMessage.pmc_id == int(pmc_id))
        .filter(PMCMessage.is_read == False)  # noqa: E712
        .scalar()
        or 0
    )
    return {"unread_count": int(count)}


@router.get("")
def list_messages(
    pmc_id: int = Query(..., description="PMC id"),
    status: str = Query("all"),   # all|open|resolved
    type: str = Query("all"),     # all|<msg_type>
    q: str = Query(""),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    qry = db.query(PMCMessage).filter(PMCMessage.pmc_id == int(pmc_id))

    if status and status != "all" and hasattr(PMCMessage, "status"):
        qry = qry.filter(PMCMessage.status == status)

    if type and type != "all":
        qry = qry.filter(PMCMessage.type == type)

    if q:
        like = f"%{q.strip().lower()}%"
        qry = qry.filter(
            func.lower(PMCMessage.subject).like(like) | func.lower(PMCMessage.body).like(like)
        )

    total = qry.count()

    rows = (
        qry.order_by(PMCMessage.id.desc())
        .limit(int(limit))
        .offset(int(offset))
        .all()
    )

    def _row(m: PMCMessage) -> dict:
        return {
            "id": int(m.id),
            "pmc_id": int(m.pmc_id),
            "dedupe_key": getattr(m, "dedupe_key", None),
            "type": getattr(m, "type", None),
            "subject": getattr(m, "subject", None),
            "body": getattr(m, "body", None),
            "severity": getattr(m, "severity", None),
            "status": getattr(m, "status", None),
            "is_read": bool(getattr(m, "is_read", False)),
            "property_id": getattr(m, "property_id", None),
            "upgrade_purchase_id": getattr(m, "upgrade_purchase_id", None),
            "upgrade_id": getattr(m, "upgrade_id", None),
            "guest_session_id": getattr(m, "guest_session_id", None),
            "link_url": getattr(m, "link_url", None),
            "created_at": getattr(m, "created_at", None).isoformat() if getattr(m, "created_at", None) else None,
        }

    return {
        "total": int(total),
        "limit": int(limit),
        "offset": int(offset),
        "items": [_row(r) for r in rows],
    }


@router.post("/{message_id}/mark-read")
def mark_read(
    message_id: int,
    db: Session = Depends(get_db),
):
    m = db.query(PMCMessage).filter(PMCMessage.id == int(message_id)).first()
    if not m:
        return {"ok": True}

    m.is_read = True
    db.add(m)
    db.commit()
    return {"ok": True}


@router.post("/{message_id}/resolve")
def resolve(
    message_id: int,
    db: Session = Depends(get_db),
):
    m = db.query(PMCMessage).filter(PMCMessage.id == int(message_id)).first()
    if not m:
        return {"ok": True}

    if hasattr(m, "status"):
        m.status = "resolved"
    db.add(m)
    db.commit()
    return {"ok": True}
