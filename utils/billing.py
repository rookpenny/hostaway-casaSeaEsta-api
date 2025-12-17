# utils/billing.py
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone, date
from typing import Optional, Tuple

import stripe
from sqlalchemy.orm import Session
from sqlalchemy import text

from models import PMC, Property


# ----------------------------
# Config + helpers
# ----------------------------
@dataclass(frozen=True)
class StripeBillingConfig:
    secret_key: str
    monthly_price_id: str


def _stripe_config() -> StripeBillingConfig:
    secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    price = (os.getenv("STRIPE_PRICE_PROPERTY_MONTHLY") or "").strip()

    if not secret:
        raise RuntimeError("Missing STRIPE_SECRET_KEY")
    if not price:
        raise RuntimeError("Missing STRIPE_PRICE_PROPERTY_MONTHLY")

    return StripeBillingConfig(secret_key=secret, monthly_price_id=price)


def month_start_utc(dt: datetime) -> date:
    """First day of the month in UTC (calendar-month billing key)."""
    dt = dt.astimezone(timezone.utc)
    return date(dt.year, dt.month, 1)


def _already_charged_this_month(db: Session, property_id: int, charge_month: date) -> bool:
    row = db.execute(
        text(
            """
            SELECT 1
            FROM property_monthly_charges
            WHERE property_id = :pid
              AND charge_month = :cm
            LIMIT 1
            """
        ),
        {"pid": int(property_id), "cm": charge_month},
    ).first()
    return bool(row)


def _record_charge(
    db: Session,
    *,
    property_id: int,
    charge_month: date,
    stripe_invoice_id: str,
    stripe_invoice_item_id: str,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO property_monthly_charges (
                property_id,
                charge_month,
                stripe_invoice_id,
                stripe_invoice_item_id,
                created_at
            )
            VALUES (:pid, :cm, :inv, :ii, NOW())
            ON CONFLICT (property_id, charge_month) DO NOTHING
            """
        ),
        {
            "pid": int(property_id),
            "cm": charge_month,
            "inv": stripe_invoice_id,
            "ii": stripe_invoice_item_id,
        },
    )


def _invoice_item_args_from_price(price_id: str) -> Tuple[dict, str]:
    """
    Stripe InvoiceItems cannot accept Prices with type=recurring.
    - If price is one_time: we can pass {"price": price_id}
    - If price is recurring: we must pass {"unit_amount": ..., "currency": ...}

    Returns:
      (invoice_item_kwargs, source_type_label)
    """
    price_obj = stripe.Price.retrieve(price_id)
    price_type = (price_obj.get("type") or "").strip().lower()  # "one_time" or "recurring"

    if price_type == "one_time":
        return {"price": price_id}, "one_time"

    # recurring => convert to a one-time invoice line using unit_amount + currency
    unit_amount = price_obj.get("unit_amount")
    currency = price_obj.get("currency")

    # Some Stripe price types (tiered/usage-based) may not have unit_amount.
    if unit_amount is None or not currency:
        raise RuntimeError(
            "STRIPE_PRICE_PROPERTY_MONTHLY is recurring but has no unit_amount/currency "
            "(tiered or metered prices can't be converted to one-time invoice items automatically)."
        )

    return {"unit_amount": int(unit_amount), "currency": str(currency)}, "recurring_as_onetime"


# ----------------------------
# Subscription quantity helper (safe no-op if unused)
# ----------------------------
def sync_subscription_quantity_for_integration(db: Session, pmc: PMC, integration_id: int) -> int:
    """
    If you are (still) using subscription quantity billing, this updates quantity for an integration.

    Safe behavior:
    - If subscription IDs are missing, this is a no-op and simply returns enabled_count.
    - If Stripe call fails, it prints and returns enabled_count (won't crash your app).
    """
    enabled_count = db.execute(
        text(
            """
            SELECT COUNT(*)
            FROM public.properties
            WHERE integration_id = :iid
              AND sandy_enabled = TRUE
            """
        ),
        {"iid": int(integration_id)},
    ).scalar_one()

    sub_id = (getattr(pmc, "stripe_subscription_id", None) or "").strip()
    item_id = (getattr(pmc, "stripe_subscription_item_id", None) or "").strip()
    if not sub_id or not item_id:
        return int(enabled_count)

    cfg = _stripe_config()
    stripe.api_key = cfg.secret_key

    try:
        stripe.Subscription.modify(
            sub_id,
            items=[{"id": item_id, "quantity": int(enabled_count)}],
            proration_behavior="none",
        )
    except Exception as e:
        print("[billing] sync_subscription_quantity_for_integration failed:", e)

    return int(enabled_count)


# ----------------------------
# Public API
# ----------------------------
def charge_property_for_month_if_needed(db: Session, pmc: PMC, prop: Property) -> bool:
    """
    RULES (calendar month):
      - If already charged this calendar month => NO charge.
      - If property is OFF => NO charge.
      - If property is turned ON mid-month and not charged this month => charge NOW.
      - Toggling OFF then back ON in same month => NO additional charge.
    """
    if not prop or not pmc:
        return False

    if not bool(getattr(prop, "sandy_enabled", False)):
        return False

    customer_id = (getattr(pmc, "stripe_customer_id", None) or "").strip()
    if not customer_id:
        return False

    cfg = _stripe_config()
    stripe.api_key = cfg.secret_key

    now = datetime.now(timezone.utc)
    cm = month_start_utc(now)

    if _already_charged_this_month(db, prop.id, cm):
        return False

    # Adapt recurring vs one_time Stripe price automatically
    price_kwargs, source_type = _invoice_item_args_from_price(cfg.monthly_price_id)

    # 1) Create invoice item (line item)
    invoice_item = stripe.InvoiceItem.create(
        customer=customer_id,
        quantity=1,
        description=f"HostScout monthly — Property {prop.id} — {cm.isoformat()}",
        metadata={
            "pmc_id": str(pmc.id),
            "property_id": str(prop.id),
            "charge_month": cm.isoformat(),
            "type": "property_monthly_charge",
            "source_price_id": cfg.monthly_price_id,
            "source_price_type": source_type,
        },
        **price_kwargs,
    )

    # 2) Create invoice + finalize (attempt charge automatically)
    invoice = stripe.Invoice.create(
        customer=customer_id,
        collection_method="charge_automatically",
        auto_advance=True,
        metadata={
            "pmc_id": str(pmc.id),
            "property_id": str(prop.id),
            "charge_month": cm.isoformat(),
            "type": "property_monthly_charge",
        },
    )

    invoice = stripe.Invoice.finalize_invoice(invoice.id)

    # 3) Persist ledger (so we never double-charge this month)
    _record_charge(
        db,
        property_id=prop.id,
        charge_month=cm,
        stripe_invoice_id=invoice.id,
        stripe_invoice_item_id=invoice_item.id,
    )

    return True


def charge_all_enabled_properties_for_month(db: Session, pmc_id: int, when: Optional[datetime] = None) -> int:
    """
    Cron/scheduler helper: charges any ENABLED properties not yet charged this calendar month.
    Returns number of charges created.
    """
    pmc = db.query(PMC).filter(PMC.id == int(pmc_id)).first()
    if not pmc:
        return 0

    when = when or datetime.now(timezone.utc)
    cm = month_start_utc(when)

    props = (
        db.query(Property)
        .filter(Property.pmc_id == pmc.id, Property.sandy_enabled.is_(True))
        .all()
    )

    charged = 0
    for prop in props:
        if _already_charged_this_month(db, prop.id, cm):
            continue
        did = charge_property_for_month_if_needed(db, pmc, prop)
        if did:
            charged += 1

    return charged
