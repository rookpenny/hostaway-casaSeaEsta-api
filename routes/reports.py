# routes/reports.py

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from database import get_db


import stripe

from datetime import date, datetime, timezone, timedelta
from typing import Optional, Tuple, Any, Dict, List


from fastapi.responses import JSONResponse

from sqlalchemy import func, case


from models import UpgradePurchase, Property, Upgrade, PMC
from routes.admin import get_user_role_and_scope

router = APIRouter()


# ----------------------------
# Helpers
# ----------------------------
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip())
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid date: {s}. Use YYYY-MM-DD.")


def _date_range(start: Optional[str], end: Optional[str]) -> Tuple[date, date]:
    """
    Returns inclusive [start, end].
    Defaults: last 30 days ending today (UTC).
    """
    today = _utc_now().date()
    d_end = _parse_date(end) or today
    d_start = _parse_date(start) or (d_end - timedelta(days=30))

    if d_start > d_end:
        raise HTTPException(status_code=400, detail="start must be <= end")

    return d_start, d_end


def _require_pmc(request: Request, db: Session) -> PMC:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_role, pmc_obj, pmc_user, billing_status, needs_payment = get_user_role_and_scope(request, db)
    if user_role != "pmc" or not pmc_obj:
        raise HTTPException(status_code=403, detail="PMC access required")

    return pmc_obj


def _require_super(request: Request, db: Session) -> None:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_role, pmc_obj, pmc_user, billing_status, needs_payment = get_user_role_and_scope(request, db)
    if user_role != "super":
        raise HTTPException(status_code=403, detail="Super access required")


def _cents(x: Any) -> int:
    try:
        return int(x or 0)
    except Exception:
        return 0




@router.get("/guest/upgrades/purchase-status")
def upgrade_purchase_status(request: Request, purchase_id: str):
    """
    Real-time verification: asks Stripe whether the checkout session is paid.
    purchase_id is the Stripe Checkout Session id (cs_...).
    """
    if not purchase_id:
        raise HTTPException(status_code=400, detail="purchase_id is required")

    try:
        sess = stripe.checkout.Session.retrieve(purchase_id)

        # Stripe fields differ a bit by mode; these are the common truthy checks:
        payment_status = (getattr(sess, "payment_status", None) or "").lower()
        status = (getattr(sess, "status", None) or "").lower()

        if payment_status == "paid":
            return {"status": "paid"}
        if status in {"open", "complete"}:
            # complete might still be unpaid in rare async cases; paid is authoritative
            return {"status": "pending"}

        return {"status": "unpaid"}

    except stripe.error.InvalidRequestError:
        # bad/unknown session id
        return JSONResponse({"status": "unknown"}, status_code=404)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to verify purchase status")


# ----------------------------
# PMC payouts (PMC only)
# ----------------------------
@router.get("/admin/reports/pmc-payouts")
def pmc_payouts_report(
    request: Request,
    start: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    end: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    property_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """
    Returns upgrade purchase payouts for the logged-in PMC only.
    """
    pmc_obj = _require_pmc(request, db)
    d_start, d_end = _date_range(start, end)

    start_dt = datetime(d_start.year, d_start.month, d_start.day, tzinfo=timezone.utc)
    end_dt = datetime(d_end.year, d_end.month, d_end.day, tzinfo=timezone.utc) + timedelta(days=1)

    q = (
        db.query(UpgradePurchase, Property, Upgrade)
        .join(Property, Property.id == UpgradePurchase.property_id)
        .join(Upgrade, Upgrade.id == UpgradePurchase.upgrade_id)
        .filter(UpgradePurchase.pmc_id == pmc_obj.id)
        # We anchor on paid_at if paid; if refunded w/o paid_at, it won't show — fine for payout reporting
        .filter(UpgradePurchase.paid_at.isnot(None))
        .filter(UpgradePurchase.paid_at >= start_dt, UpgradePurchase.paid_at < end_dt)
        .order_by(UpgradePurchase.paid_at.desc())
    )

    if property_id:
        q = q.filter(UpgradePurchase.property_id == property_id)

    rows = []
    gross_cents = 0
    fee_cents = 0
    paid_count = 0
    refunded_count = 0

    for p, prop, up in q.all():
        amount = _cents(getattr(p, "amount_cents", 0))
        fee = _cents(getattr(p, "platform_fee_cents", 0))
        status = (getattr(p, "status", "") or "").lower()

        gross_cents += amount
        fee_cents += fee

        if status == "paid":
            paid_count += 1
        elif status == "refunded":
            refunded_count += 1

        paid_at = getattr(p, "paid_at", None)
        rows.append(
            {
                "purchase_id": p.id,
                "paid_at": paid_at.isoformat() if paid_at else None,
                "status": status or "pending",
                "property_id": prop.id if prop else getattr(p, "property_id", None),
                "property_name": getattr(prop, "property_name", None),
                "upgrade_id": up.id if up else getattr(p, "upgrade_id", None),
                "upgrade_title": getattr(up, "title", None),
                "amount_cents": amount,
                "platform_fee_cents": fee,
                "net_cents": max(0, amount - fee),
                # helpful reconciliation fields if you added them:
                "stripe_checkout_session_id": getattr(p, "stripe_checkout_session_id", None),
                "stripe_payment_intent_id": getattr(p, "stripe_payment_intent_id", None),
                "stripe_transfer_id": getattr(p, "stripe_transfer_id", None),
            }
        )

    return {
        "range": {"start": d_start.isoformat(), "end": d_end.isoformat()},
        "pmc": {"id": pmc_obj.id, "name": getattr(pmc_obj, "pmc_name", None), "email": getattr(pmc_obj, "email", None)},
        "summary": {
            "gross_cents": gross_cents,
            "hostscout_fee_cents": fee_cents,
            "net_to_pmc_cents": max(0, gross_cents - fee_cents),
            "paid_count": paid_count,
            "refunded_count": refunded_count,
        },
        "rows": rows,
    }


# ----------------------------
# HostScout revenue (super only)
# ----------------------------
@router.get("/admin/reports/hostscout-revenue")
def hostscout_revenue_report(
    request: Request,
    start: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    end: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """
    Super-only rollup across all PMCs, based on UpgradePurchase rows.
    """
    _require_super(request, db)
    d_start, d_end = _date_range(start, end)

    start_dt = datetime(d_start.year, d_start.month, d_start.day, tzinfo=timezone.utc)
    end_dt = datetime(d_end.year, d_end.month, d_end.day, tzinfo=timezone.utc) + timedelta(days=1)

    # Aggregate by pmc_id
    agg = (
        db.query(
            UpgradePurchase.pmc_id.label("pmc_id"),
            func.coalesce(func.sum(UpgradePurchase.amount_cents), 0).label("gross_cents"),
            func.coalesce(func.sum(UpgradePurchase.platform_fee_cents), 0).label("hostscout_fee_cents"),
            func.coalesce(func.sum(UpgradePurchase.amount_cents - UpgradePurchase.platform_fee_cents), 0).label("net_to_pmc_cents"),
            func.coalesce(func.sum(case((func.lower(UpgradePurchase.status) == "paid", 1), else_=0)), 0).label("paid_count"),
            func.coalesce(func.sum(case((func.lower(UpgradePurchase.status) == "refunded", 1), else_=0)), 0).label("refunded_count"),
        )
        .filter(UpgradePurchase.paid_at.isnot(None))
        .filter(UpgradePurchase.paid_at >= start_dt, UpgradePurchase.paid_at < end_dt)
        .group_by(UpgradePurchase.pmc_id)
        .order_by(func.coalesce(func.sum(UpgradePurchase.platform_fee_cents), 0).desc())
        .all()
    )

    pmc_ids = [int(r.pmc_id) for r in agg if r.pmc_id is not None]
    pmc_map: Dict[int, Dict[str, Any]] = {}

    if pmc_ids:
        for p in db.query(PMC).filter(PMC.id.in_(pmc_ids)).all():
            pmc_map[p.id] = {"id": p.id, "name": getattr(p, "pmc_name", None), "email": getattr(p, "email", None)}

    by_pmc = []
    total_gross = 0
    total_fee = 0
    total_net = 0
    total_paid = 0
    total_refunded = 0

    for r in agg:
        pmc_id = int(r.pmc_id) if r.pmc_id is not None else None
        gross = _cents(r.gross_cents)
        fee = _cents(r.hostscout_fee_cents)
        net = _cents(r.net_to_pmc_cents)

        total_gross += gross
        total_fee += fee
        total_net += net
        total_paid += int(r.paid_count or 0)
        total_refunded += int(r.refunded_count or 0)

        by_pmc.append(
            {
                "pmc_id": pmc_id,
                "pmc": pmc_map.get(pmc_id) if pmc_id else None,
                "gross_cents": gross,
                "hostscout_fee_cents": fee,
                "net_to_pmc_cents": net,
                "paid_count": int(r.paid_count or 0),
                "refunded_count": int(r.refunded_count or 0),
            }
        )

    return {
        "range": {"start": d_start.isoformat(), "end": d_end.isoformat()},
        "summary": {
            "gross_cents": total_gross,
            "hostscout_fee_cents": total_fee,
            "net_to_pmc_cents": total_net,
            "paid_count": total_paid,
            "refunded_count": total_refunded,
        },
        "by_pmc": by_pmc,
    }
