# routes/hostscout_revenue.py
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from database import get_db
from models import UpgradePurchase, PMC
from routes.admin import get_user_role_and_scope  # you already have this

router = APIRouter()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ymd(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def require_admin_scope(request: Request, db: Session):
    """
    Adjust this to your auth model. The point: only platform/admin can view HostScout revenue.
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_role, pmc_obj, pmc_user, billing_status, needs_payment = get_user_role_and_scope(request, db)

    # ✅ allow only admin/platform
    if user_role not in {"admin", "platform"}:
        raise HTTPException(status_code=403, detail="Admin access required")

    return True


@router.get("/admin/reports/hostscout-revenue")
def hostscout_revenue_report(
    request: Request,
    start: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    end: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    require_admin_scope(request, db)

    start_d = _parse_ymd(start)
    end_d = _parse_ymd(end)

    # Default: last 30 days
    if not end_d:
        end_d = _utc_now().date()
    if not start_d:
        start_d = end_d - timedelta(days=30)

    # Inclusive date range -> [start 00:00, end+1 00:00)
    start_dt = datetime(start_d.year, start_d.month, start_d.day, tzinfo=timezone.utc)
    end_dt = datetime(end_d.year, end_d.month, end_d.day, tzinfo=timezone.utc) + timedelta(days=1)

    # Only rows with paid_at in range (paid or refunded, both were paid at some point)
    q = (
        db.query(UpgradePurchase)
        .filter(UpgradePurchase.paid_at.isnot(None))
        .filter(UpgradePurchase.paid_at >= start_dt)
        .filter(UpgradePurchase.paid_at < end_dt)
    )

    # Summary totals:
    # - gross: sum(amount_cents) for status=paid minus sum(amount_cents) for status=refunded
    # - hostscout_fee: sum(platform_fee_cents) for paid minus for refunded
    # - net_to_pmc: gross - hostscout_fee
    gross_expr = func.coalesce(func.sum(
        case(
            (func.lower(UpgradePurchase.status) == "paid", UpgradePurchase.amount_cents),
            (func.lower(UpgradePurchase.status) == "refunded", -UpgradePurchase.amount_cents),
            else_=0,
        )
    ), 0)

    fee_expr = func.coalesce(func.sum(
        case(
            (func.lower(UpgradePurchase.status) == "paid", UpgradePurchase.platform_fee_cents),
            (func.lower(UpgradePurchase.status) == "refunded", -UpgradePurchase.platform_fee_cents),
            else_=0,
        )
    ), 0)

    count_paid_expr = func.coalesce(func.sum(
        case((func.lower(UpgradePurchase.status) == "paid", 1), else_=0)
    ), 0)

    count_ref_expr = func.coalesce(func.sum(
        case((func.lower(UpgradePurchase.status) == "refunded", 1), else_=0)
    ), 0)

    summary = (
        db.query(
            gross_expr.label("gross_cents"),
            fee_expr.label("hostscout_fee_cents"),
            count_paid_expr.label("paid_count"),
            count_ref_expr.label("refunded_count"),
        )
        .select_from(UpgradePurchase)
        .filter(UpgradePurchase.paid_at.isnot(None))
        .filter(UpgradePurchase.paid_at >= start_dt)
        .filter(UpgradePurchase.paid_at < end_dt)
        .one()
    )

    gross_cents = int(summary.gross_cents or 0)
    fee_cents = int(summary.hostscout_fee_cents or 0)
    net_cents = gross_cents - fee_cents

    # Breakdown by PMC
    rows = (
        db.query(
            UpgradePurchase.pmc_id.label("pmc_id"),
            func.coalesce(func.sum(
                case(
                    (func.lower(UpgradePurchase.status) == "paid", UpgradePurchase.amount_cents),
                    (func.lower(UpgradePurchase.status) == "refunded", -UpgradePurchase.amount_cents),
                    else_=0,
                )
            ), 0).label("gross_cents"),
            func.coalesce(func.sum(
                case(
                    (func.lower(UpgradePurchase.status) == "paid", UpgradePurchase.platform_fee_cents),
                    (func.lower(UpgradePurchase.status) == "refunded", -UpgradePurchase.platform_fee_cents),
                    else_=0,
                )
            ), 0).label("hostscout_fee_cents"),
            func.coalesce(func.sum(
                case((func.lower(UpgradePurchase.status) == "paid", 1), else_=0)
            ), 0).label("paid_count"),
            func.coalesce(func.sum(
                case((func.lower(UpgradePurchase.status) == "refunded", 1), else_=0)
            ), 0).label("refunded_count"),
        )
        .filter(UpgradePurchase.paid_at.isnot(None))
        .filter(UpgradePurchase.paid_at >= start_dt)
        .filter(UpgradePurchase.paid_at < end_dt)
        .group_by(UpgradePurchase.pmc_id)
        .order_by(func.sum(
            case(
                (func.lower(UpgradePurchase.status) == "paid", UpgradePurchase.platform_fee_cents),
                (func.lower(UpgradePurchase.status) == "refunded", -UpgradePurchase.platform_fee_cents),
                else_=0,
            )
        ).desc())
        .all()
    )

    # Attach PMC names (avoid N+1)
    pmc_ids = [int(r.pmc_id) for r in rows if r.pmc_id is not None]
    pmcs = {}
    if pmc_ids:
        for p in db.query(PMC).filter(PMC.id.in_(pmc_ids)).all():
            pmcs[int(p.id)] = {
                "id": int(p.id),
                "name": getattr(p, "name", None) or getattr(p, "company_name", None) or f"PMC {p.id}",
                "email": getattr(p, "email", None),
            }

    breakdown = []
    for r in rows:
        pmc_id = int(r.pmc_id) if r.pmc_id is not None else None
        gross = int(r.gross_cents or 0)
        fee = int(r.hostscout_fee_cents or 0)
        breakdown.append({
            "pmc_id": pmc_id,
            "pmc": pmcs.get(pmc_id) if pmc_id else None,
            "gross_cents": gross,
            "hostscout_fee_cents": fee,
            "net_to_pmc_cents": gross - fee,
            "paid_count": int(r.paid_count or 0),
            "refunded_count": int(r.refunded_count or 0),
        })

    return JSONResponse({
        "range": {"start": start_d.isoformat(), "end": end_d.isoformat()},
        "summary": {
            "gross_cents": gross_cents,
            "hostscout_fee_cents": fee_cents,
            "net_to_pmc_cents": net_cents,
            "paid_count": int(summary.paid_count or 0),
            "refunded_count": int(summary.refunded_count or 0),
        },
        "by_pmc": breakdown,
    })
