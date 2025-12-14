# utils/billing.py
import os
import stripe
from sqlalchemy.orm import Session
from sqlalchemy import func

from models import PMC, Property


def _stripe_config() -> tuple[str, str]:
    stripe_secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    price_property = (os.getenv("STRIPE_PRICE_PROPERTY_MONTHLY") or "").strip()
    return stripe_secret, price_property


def _count_billable(db: Session, pmc_id: int) -> int:
    billable = (
        db.query(func.count(Property.id))
        .filter(Property.pmc_id == pmc_id, Property.sandy_enabled.is_(True))
        .scalar()
    ) or 0
    return int(billable)


def sync_property_quantity(db: Session, pmc_id: int, proration_behavior: str = "none") -> int:
    """
    Policy implemented:
    - Quantity changes apply at NEXT renewal (no mid-cycle charge/refund) via proration_behavior="none".
    - If billable count becomes 0 => set subscription cancel_at_period_end=True.
    - If billable count becomes >0 => ensure cancel_at_period_end=False.
    - If subscription is already canceled/ended, we do NOT create a new one here; we return billable.
      (Caller should send user to subscription checkout when re-enabling after full cancel.)
    """
    stripe_secret, price_property = _stripe_config()
    if not stripe_secret or not price_property:
        return 0

    stripe.api_key = stripe_secret

    pmc = db.query(PMC).filter(PMC.id == pmc_id).first()
    if not pmc:
        return 0

    # They keep access even if no subscription; only gate billing changes
    if (getattr(pmc, "billing_status", "") or "").strip().lower() != "active":
        return 0

    subscription_id = getattr(pmc, "stripe_subscription_id", None)
    if not subscription_id:
        # No subscription yet (or cleared) — caller should create one when enabling properties.
        return _count_billable(db, pmc_id)

    billable = _count_billable(db, pmc_id)

    # Retrieve subscription (we need status + items)
    sub = stripe.Subscription.retrieve(subscription_id, expand=["items.data.price"])

    status = (sub.get("status") or "").lower()
    # If it's fully canceled/ended, do not modify; caller should create a new subscription
    if status in {"canceled", "incomplete_expired"}:
        return billable

    # Find or create the subscription item for our per-property price
    item_id = getattr(pmc, "stripe_subscription_item_id", None)

    # Validate cached item_id (it might be stale)
    if item_id:
        found = False
        for it in (sub.get("items") or {}).get("data", []):
            if it.get("id") == item_id:
                found = True
                break
        if not found:
            item_id = None

    if not item_id:
        for it in (sub.get("items") or {}).get("data", []):
            price = it.get("price") or {}
            if price.get("id") == price_property:
                item_id = it.get("id")
                break

    if not item_id:
        # If subscription doesn't yet have the per-property line item, add it
        created = stripe.SubscriptionItem.create(
            subscription=subscription_id,
            price=price_property,
            quantity=billable,
            proration_behavior=proration_behavior,
        )
        item_id = created.get("id")

    # 1) Update quantity (no proration => applies next invoice)
    stripe.SubscriptionItem.modify(
        item_id,
        quantity=billable,
        proration_behavior=proration_behavior,
    )

    # 2) Cancel behavior
    # If they turned OFF all properties, cancel at period end (so no renewal).
    # If they turned ON any property again, ensure it will renew.
    if billable <= 0:
        if not sub.get("cancel_at_period_end", False):
            stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
    else:
        if sub.get("cancel_at_period_end", False):
            stripe.Subscription.modify(subscription_id, cancel_at_period_end=False)

    # Cache item id for next time
    if hasattr(pmc, "stripe_subscription_item_id"):
        pmc.stripe_subscription_item_id = item_id
    db.commit()

    return billable
