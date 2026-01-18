# routes/upgrade_purchase_status.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import UpgradePurchase

router = APIRouter()


@router.get("/guest/upgrades/purchase-status")
def get_upgrade_purchase_status(
    purchase_id: int = Query(..., description="UpgradePurchase.id"),
    db: Session = Depends(get_db),
):
    """
    Returns the status of an upgrade purchase so the guest UI can confirm payment.
    """
    p = db.query(UpgradePurchase).filter(UpgradePurchase.id == purchase_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Purchase not found")

    return {
        "purchase_id": p.id,
        "status": (p.status or "pending"),
        "amount_cents": int(p.amount_cents or 0),
        "refunded_amount_cents": int(getattr(p, "refunded_amount_cents", 0) or 0),
        "paid_at": p.paid_at.isoformat() if getattr(p, "paid_at", None) else None,
        "refunded_at": p.refunded_at.isoformat() if getattr(p, "refunded_at", None) else None,
    }


@router.get("/guest/upgrades/purchase-status/by-session")
def get_upgrade_purchase_status_by_session(
    session_id: str = Query(..., description="Stripe Checkout Session id (cs_...)"),
    db: Session = Depends(get_db),
):
    """
    Optional helper: look up a purchase by Stripe checkout session id.
    Useful if you only have ?session_id=cs_... after redirect.
    """
    p = (
        db.query(UpgradePurchase)
        .filter(UpgradePurchase.stripe_checkout_session_id == session_id)
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="Purchase not found")

    return {
        "purchase_id": p.id,
        "status": (p.status or "pending"),
        "amount_cents": int(p.amount_cents or 0),
        "refunded_amount_cents": int(getattr(p, "refunded_amount_cents", 0) or 0),
        "paid_at": p.paid_at.isoformat() if getattr(p, "paid_at", None) else None,
        "refunded_at": p.refunded_at.isoformat() if getattr(p, "refunded_at", None) else None,
    }
