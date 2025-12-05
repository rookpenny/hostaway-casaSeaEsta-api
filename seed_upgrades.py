from database import SessionLocal
from models import Upgrade

db = SessionLocal()

PROPERTY_ID = 1  # change to your real property ID

upgrades = [
    Upgrade(
        property_id=PROPERTY_ID,
        slug="early-check-in",
        title="Early check-in",
        short_description="Arrive earlier and start relaxing sooner.",
        long_description="Check in as early as 1:00 PM (subject to availability).",
        price_cents=7500,
        currency="usd",
        sort_order=1,
    ),
    Upgrade(
        property_id=PROPERTY_ID,
        slug="purchase-groceries",
        title="Purchase groceries",
        short_description="Have essentials waiting for you.",
        long_description="Send us your list and we'll stock the fridge before you arrive.",
        price_cents=9500,
        currency="usd",
        sort_order=2,
    ),
    Upgrade(
        property_id=PROPERTY_ID,
        slug="mid-stay-clean",
        title="Mid-stay clean",
        short_description="Reset and refresh your space mid-stay.",
        long_description="A full clean including bathrooms, surfaces, and linens.",
        price_cents=12000,
        currency="usd",
        sort_order=3,
    ),
    Upgrade(
        property_id=PROPERTY_ID,
        slug="late-check-out",
        title="Late check-out",
        short_description="Enjoy a slower final morning.",
        long_description="Extend checkout time to relax without rushing.",
        price_cents=6000,
        currency="usd",
        sort_order=4,
    ),
]

db.add_all(upgrades)
db.commit()
db.close()

print("Seeded upgrades successfully!")
