# utils/billing.py
import os
import stripe
from sqlalchemy.orm import Session
from sqlalchemy import func

from models import PMC, Property

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
PRICE_PROPERTY = os.getenv("STRIPE_PRICE_PROPERTY_MONTHLY")

def sync_property_quantity(db: Session, pmc_id: int, proration_behavior: str = "none") -> int:
    pmc = db.query(PMC).filter(PMC.id == pmc_id).first()
    if not pmc or pmc.billing_status != "active":
        return 0
    if not pmc.stripe_subscription_id or not PRICE_PROPERTY:
        return 0

    billable = (
        db.query(func.count(Property.id))
        .filter(Property.pmc_id == pmc_id, Property.sandy_enabled == True)
        .scalar()
    ) or 0

    # Find the subscription item for the per-property price
    sub = stripe.Subscription.retrieve(pmc.stripe_subscription_id, expand=["items.data.price"])
    item_id = None
    for it in sub["items"]["data"]:
        if it["price"]["id"] == PRICE_PROPERTY:
            item_id = it["id"]
            break

    if not item_id:
        return int(billable)

    stripe.SubscriptionItem.modify(
        item_id,
        quantity=int(billable),
        proration_behavior=proration_behavior,
    )

    # Optional: store for faster future updates
    pmc.stripe_subscription_item_id = item_id
    db.commit()

    return int(billable)
