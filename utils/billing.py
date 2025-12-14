# utils/billing.py
import os
import stripe
from sqlalchemy.orm import Session
from sqlalchemy import func

from models import PMC, Property


def _stripe_config() -> tuple[str, str]:
    """
    Load Stripe config at runtime (safer than import-time).
    Returns (stripe_secret_key, per_property_price_id).
    """
    stripe_secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    price_property = (os.getenv("STRIPE_PRICE_PROPERTY_MONTHLY") or "").strip()
    return stripe_secret, price_property


def sync_property_quantity(db: Session, pmc_id: int, proration_behavior: str = "none") -> int:
    """
    Ensures the Stripe subscription quantity matches the number of sandy_enabled properties.

    - Requires PMC.billing_status == "active"
    - Requires PMC.stripe_subscription_id
    - Requires STRIPE_PRICE_PROPERTY_MONTHLY env var to be set

    Returns the billable quantity (int).
    """
    stripe_secret, price_property = _stripe_config()
    if not stripe_secret or not price_property:
        return 0

    stripe.api_key = stripe_secret

    pmc = db.query(PMC).filter(PMC.id == pmc_id).first()
    if not pmc:
        return 0

    if (getattr(pmc, "billing_status", "") or "").strip().lower() != "active":
        return 0

    subscription_id = getattr(pmc, "stripe_subscription_id", None)
    if not subscription_id:
        return 0

    # Count billable properties (enabled == True)
    billable = (
        db.query(func.count(Property.id))
        .filter(Property.pmc_id == pmc_id, Property.sandy_enabled.is_(True))
        .scalar()
    ) or 0
    billable = int(billable)

    # 1) Try the cached subscription item id first (fast path)
    cached_item_id = getattr(pmc, "stripe_subscription_item_id", None)
    if cached_item_id:
        try:
            stripe.SubscriptionItem.modify(
                cached_item_id,
                quantity=billable,
                proration_behavior=proration_behavior,
            )
            return billable
        except Exception:
            # Cached item might be stale; fall through to re-discover
            pass

    # 2) Retrieve subscription + locate correct item by price id
    sub = stripe.Subscription.retrieve(subscription_id, expand=["items.data.price"])

    item_id = None
    for it in (sub.get("items") or {}).get("data", []):
        price = (it.get("price") or {})
        if price.get("id") == price_property:
            item_id = it.get("id")
            break

    # 3) If item doesn't exist yet, ADD it to the subscription
    if not item_id:
        added = stripe.SubscriptionItem.create(
            subscription=subscription_id,
            price=price_property,
            quantity=billable,
            proration_behavior=proration_behavior,
        )
        item_id = added.get("id")

    # 4) Update quantity (even if we just created it, this is safe)
    stripe.SubscriptionItem.modify(
        item_id,
        quantity=billable,
        proration_behavior=proration_behavior,
    )

    # Cache for faster future updates (only if your model has the column)
    if hasattr(pmc, "stripe_subscription_item_id"):
        pmc.stripe_subscription_item_id = item_id
        db.commit()

    return billable
