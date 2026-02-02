# routes/admin_messages.py
from __future__ import annotations

from typing import Optional, Literal

from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from database import get_db
from models import PMCMessage, PMCUser

router = APIRouter(prefix="/admin/messages", tags=["admin-messages"])


# -------------------------
# Helpers: resolve PMC context
# -------------------------
def _get_current_admin(db: Session, request: Request) -> PMCUser:
    """
    Adjust this to match YOUR auth.
    This assumes you store admin user id in session as request.session["pmc_user_id"].
    """
    user_id = request.session.get("pmc_user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    u = db.query(PMCUser).filter(PMCUser.id == int(user_id), PMCUser.is_active == True).first()  # noqa: E712
    if not u:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return u


def _resolve_pmc_id(
    db: Session,
    request: Request,
    pmc_id: Optional[int],
) -> int:
    """
    If pmc_id missing: infer from current admin user.
    If pmc_id provided: only allow if superuser (prevents cross-PMC access).
    """
    admin = _get_current_admin(db, request)

    admin_pmc_id = int(getattr(admin, "pmc_id", 0) or 0)
    is_super = bool(getattr(admin, "is_superuser", False))

    if pmc_id is None:
        if not admin_pmc_id:
            raise HTTPException(status_code=400, detail="No PMC context available")
        return admin_pmc_id

    # pmc_id explicitly provided
    if not is_super and pmc_id != admin_pmc_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    return int(pmc_id)


# -------------------------
# Routes
# -------------------------
@router.get("/unread-count")
def unread_count(
    request: Request,
    pmc_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    resolved_pmc_id = _resolve_pmc_id(db, request, pmc_id)

    n = (
        db.query(func.count(PMCMessage.id))
        .filter(PMCMessage.pmc_id == resolved_pmc_id)
        .filter(PMCMessage.is_read == False)  # noqa: E712
        .scalar()
        or 0
    )
    return {"unread": int(n)}


@router.get("")
def list_messages(
    request: Request,
    pmc_id: Optional[int] = Query(default=None),
    status: str = Query(default="all"),
    type: str = Query(default="all"),
    q: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    resolved_pmc_id = _resolve_pmc_id(db, request, pmc_id)

    qry = db.query(PMCMessage).filter(PMCMessage.pmc_id == resolved_pmc_id)

    if status != "all" and hasattr(PMCMessage, "status"):
        qry = qry.filter(PMCMessage.status == status)

    if type != "all":
        qry = qry.filter(PMCMessage.type == type)

    if q:
        like = f"%{q.strip().lower()}%"
        qry = qry.filter(
            func.lower(PMCMessage.subject).like(like) | func.lower(PMCMessage.body).like(like)
        )

    total = qry.count()

    rows = (
        qry.order_by(desc(PMCMessage.created_at) if hasattr(PMCMessage, "created_at") else desc(PMCMessage.id))
        .limit(limit)
        .offset(offset)
        .all()
    )

    def _row(m: PMCMessage) -> dict:
        return {
            "id": int(m.id),
            "type": getattr(m, "type", None),
            "subject": getattr(m, "subject", ""),
            "body": getattr(m, "body", ""),
            "severity": getattr(m, "severity", "info"),
            "status": getattr(m, "status", "open"),
            "is_read": bool(getattr(m, "is_read", False)),
            "property_id": getattr(m, "property_id", None),
            "upgrade_purchase_id": getattr(m, "upgrade_purchase_id", None),
            "upgrade_id": getattr(m, "upgrade_id", None),
            "guest_session_id": getattr(m, "guest_session_id", None),
            "link_url": getattr(m, "link_url", None),
            "created_at": getattr(m, "created_at", None),
        }

    return {"total": int(total), "items": [_row(r) for r in rows]}
