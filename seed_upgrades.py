# seed_upgrades.py
import os
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Upgrade

# CHANGE THIS to the property you want to seed
PROPERTY_ID = 5

UPGRADES = [
    {
        "title": "Early Check-in",
        "slug": "early-check-in",
        "short_description": "Arrive early and start relaxing sooner.",
        "long_description": "Arrive early and start relaxing sooner with guaranteed early access to the property.",
        "price_cents": 3500,
    },
    {
        "title": "Purchase Groceries",
        "slug": "groceries",
        "short_description": "Let us stock the fridge before you arrive.",
        "long_description": "Send us your list and we'll have your favorite groceries ready and waiting when you arrive.",
        "price_cents": 6000,
    },
    {
        "title": "Mid-Stay Clean",
        "slug": "mid-stay-clean",
        "short_description": "A fresh clean during your stay.",
        "long_description": "Enjoy fresh towels, linens, and a tidy space with a full clean during your stay.",
        "price_cents": 8500,
    },
    {
        "title": "Late Checkout",
        "slug": "late-checkout",
        "short_description": "Enjoy a slower, more relaxed departure.",
        "long_description": "Extend your stay a few extra hours so you can pack up and head out at your own pace.",
        "price_cents": 3000,
    },
]

def run():
    db: Session = SessionLocal()

    for idx, u in enumerate(UPGRADES):
        existing = (
            db.query(Upgrade)
            .filter(
                Upgrade.property_id == PROPERTY_ID,
                Upgrade.slug == u["slug"],
            )
            .first()
        )

        if existing:
            print(f"Already exists → {u['title']}")
            continue

        upgrade = Upgrade(
            property_id=PROPERTY_ID,
            title=u["title"],
            slug=u["slug"],
            short_description=u["short_description"],
            long_description=u.get("long_description"),
            price_cents=u["price_cents"],
            currency="usd",          # adjust if your app uses a different default
            is_active=True,
            sort_order=idx + 1,      # keeps them ordered
            stripe_price_id=None,    # fill this in later after creating Stripe prices
        )

        db.add(upgrade)
        print(f"Created upgrade → {u['title']}")

    db.commit()
    db.close()
    print("✅ Done seeding upgrades!")

if __name__ == "__main__":
    run()
