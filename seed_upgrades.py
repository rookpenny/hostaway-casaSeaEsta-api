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
        "description": "Arrive early and start relaxing sooner.",
        "price_cents": 3500,
    },
    {
        "title": "Purchase Groceries",
        "slug": "groceries",
        "description": "Let us stock the fridge before you arrive.",
        "price_cents": 6000,
    },
    {
        "title": "Mid-Stay Clean",
        "slug": "mid-stay-clean",
        "description": "A fresh clean during your stay.",
        "price_cents": 8500,
    },
    {
        "title": "Late Checkout",
        "slug": "late-checkout",
        "description": "Enjoy a slower, more relaxed departure.",
        "price_cents": 3000,
    },
]

def run():
    db: Session = SessionLocal()

    for u in UPGRADES:
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
            description=u["description"],
            price_cents=u["price_cents"],
            is_active=True,
            stripe_price_id=None,  # You will fill this in after creating Stripe prices
        )

        db.add(upgrade)
        print(f"Created upgrade → {u['title']}")

    db.commit()
    db.close()
    print("✅ Done seeding upgrades!")

if __name__ == "__main__":
    run()
