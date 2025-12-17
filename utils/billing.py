# utils/billing.py
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone, date
from typing import Optional

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
        text("""
            SELECT 1
            FROM property_monthly_charges
            WHERE property_id = :pid
              AND charge_month = :cm
            LIMIT 1
        """),
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
        text("""
            INSERT INTO property_monthly_charges (
                property_id,
                charge_month,
                stripe_invoice_id,
                stripe_invoice_item_id,
                created_at
            )
            VALUES (:pid, :cm, :inv, :ii, NOW())
            ON CONFLICT (property_id, charge_month) DO NOTHING
        """),
        {
            "pid": int(property_id),
            "cm": charge_month,
            "inv": stripe_invoice_id,
            "ii": stripe_invoice_item_id,
        },
    )

def sync_subscription_quantity_for_integration(db: Session, pmc: PMC, integration_id: int) -> int:
    """
    If you're using a Stripe subscription-based per-property model:
    Set Stripe subscription quantity = number of enabled properties for THIS integration.

    Uses proration_behavior='none' so changes apply next renewal (no mid-cycle charge/refund).
    Returns enabled_count.

    NOTE:
    - This only works if pmc.stripe_subscription_id and pmc.stripe_subscription_item_id are set.
    - If you aren't using subscription quantities anymore (because you're invoicing per-property),
      you can still keep this function just to satisfy imports, and it will no-op safely.
    """
    enabled_count = db.execute(
        text("""
            SELECT COUNT(*)
            FROM public.properties
            WHERE integration_id = :iid
              AND sandy_enabled = TRUE
        """),
        {"iid": int(integration_id)},
    ).scalar_one()

    sub_id = (getattr(pmc, "stripe_subscription_id", None) or "").strip()
    item_id = (getattr(pmc, "stripe_subscription_item_id", None) or "").strip()

    # If you don't have a subscription wired up, just return the count (no-op)
    if not sub_id or not item_id:
        return int(enabled_count)

    cfg = _stripe_config()
    stripe.api_key = cfg.secret_key

    stripe.Subscription.modify(
        sub_id,
        items=[{"id": item_id, "quantity": int(enabled_count)}],
        proration_behavior="none",
    )

    return int(enabled_count)


# ----------------------------
# Public API
# ----------------------------
def charge_property_for_month_if_needed(db: Session, pmc: PMC, prop: Property) -> bool:
    """
    RULES (calendar month):
      - If the property was already charged in this calendar month => NO charge.
      - If property is OFF => NO charge.
      - If property is turned ON mid-month and not charged this month => charge NOW.
      - Toggling OFF then back ON in same month => NO additional charge.
    Returns:
      True if we charged now, False otherwise.
    """
    if not prop or not pmc:
        return False

    # Only charge when it's actually ON
    if not bool(getattr(prop, "sandy_enabled", False)):
        return False

    # PMC must have Stripe customer
    customer_id = (getattr(pmc, "stripe_customer_id", None) or "").strip()
    if not customer_id:
        return False

    cfg = _stripe_config()
    stripe.api_key = cfg.secret_key

    now = datetime.now(timezone.utc)
    cm = month_start_utc(now)

    # Idempotent check (ledger)
    if _already_charged_this_month(db, prop.id, cm):
        return False

    # Create invoice item for this one property for this month
    invoice_item = stripe.InvoiceItem.create(
        customer=customer_id,
        price=cfg.monthly_price_id,
        quantity=1,
        description=f"HostScout monthly — Property {prop.id} — {cm.isoformat()}",
    )

    # Create invoice + finalize (attempt charge automatically)
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

    # Persist ledger (so we never double-charge this month)
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
    Use this for a cron/scheduler job (e.g. daily).
    It charges any ENABLED properties that have NOT been charged yet this calendar month.

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
