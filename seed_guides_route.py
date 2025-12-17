# seed_guides_route.py
"""
Browser-accessible endpoint to:
1) Create the 'guides' table if missing (FK -> properties.id which is INTEGER)
2) Insert/Upsert sample guides for a chosen property_id (idempotent)

Visit:
    /seed-guides?property_id=1
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db

router = APIRouter()

# --- SQL for creating table (matches your properties.id = INTEGER) ---
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS guides (
    id SERIAL PRIMARY KEY,
    property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,

    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT,
    short_description TEXT,
    long_description TEXT,
    body_html TEXT,
    image_url TEXT,

    is_active BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

# Make seeding idempotent via upsert (requires a unique constraint/index)
ENSURE_UNIQUE_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND indexname = 'ux_guides_property_slug'
    ) THEN
        CREATE UNIQUE INDEX ux_guides_property_slug ON guides (property_id, slug);
    END IF;
END $$;
"""

UPSERT_GUIDES_SQL = """
INSERT INTO guides (
    property_id, slug, title, category,
    short_description, long_description, image_url,
    is_active, sort_order, updated_at
)
VALUES
(
    :property_id,
    'best-coffee',
    'Best Coffee Within 10 Minutes',
    'Food & Drink',
    'Our 3 favourite coffee stops nearby.',
    '1) Shoreline Coffee — 5 min walk
2) Marina Roasters — 8 min drive
3) Harbor Bakery — great pastries',
    'https://placehold.co/600x400',
    TRUE,
    1,
    NOW()
),
(
    :property_id,
    'family-afternoon',
    'Kid-Friendly Afternoon',
    'Family',
    'Playground + ice cream combo.',
    'Start at Harbor Park playground, then walk to Scoops & Smiles ice cream.',
    'https://placehold.co/600x400',
    TRUE,
    2,
    NOW()
),
(
    :property_id,
    'rainy-day',
    'Rainy Day Ideas',
    'Things to do',
    'Indoor activities nearby.',
    'Visit local museum, small cinema, or board-game café.',
    'https://placehold.co/600x400',
    TRUE,
    3,
    NOW()
),
(
    :property_id,
    'sunset-spots',
    'Best Sunset Spots',
    'Outdoors',
    'Where to catch golden hour near the property.',
    '1) West Point overlook — best panoramic view
2) Seaside steps — bring a blanket
3) Harbor pier — great photos',
    'https://placehold.co/600x400',
    TRUE,
    4,
    NOW()
),
(
    :property_id,
    'easy-hikes',
    'Easy Hikes for Any Fitness Level',
    'Outdoors',
    'Three low-effort trails with big payoff.',
    'Try the Bay Loop (flat), Lighthouse Trail (views), or Creek Walk (shade).',
    'https://placehold.co/600x400',
    TRUE,
    5,
    NOW()
),
(
    :property_id,
    'date-night',
    'Perfect Date Night',
    'Food & Drink',
    'Dinner + a nightcap that feels special.',
    'Book a table at a cozy bistro, then head to a quiet cocktail bar or wine lounge.',
    'https://placehold.co/600x400',
    TRUE,
    6,
    NOW()
),
(
    :property_id,
    'local-markets',
    'Local Markets & Fresh Finds',
    'Shopping',
    'Best spots for produce, snacks, and souvenirs.',
    'Visit the weekend farmers market, the neighborhood grocer, and the artisan shop street.',
    'https://placehold.co/600x400',
    TRUE,
    7,
    NOW()
),
(
    :property_id,
    'emergency-essentials',
    'Emergency Essentials Nearby',
    'Helpful Info',
    'Pharmacy, urgent care, and 24/7 basics.',
    'Nearest pharmacy and urgent care are a short drive away. For true emergencies, call local emergency services.',
    'https://placehold.co/600x400',
    TRUE,
    8,
    NOW()
)
ON CONFLICT (property_id, slug) DO UPDATE SET
    title = EXCLUDED.title,
    category = EXCLUDED.category,
    short_description = EXCLUDED.short_description,
    long_description = EXCLUDED.long_description,
    image_url = EXCLUDED.image_url,
    is_active = EXCLUDED.is_active,
    sort_order = EXCLUDED.sort_order,
    updated_at = NOW();
"""


@router.get("/seed-guides")
def seed_guides(
    property_id: int = Query(..., description="ID of the property to attach guides to"),
    db: Session = Depends(get_db),
):
    """
    Visit in browser:
        /seed-guides?property_id=1
    """
    try:
        # Ensure property exists
        exists = db.execute(
            text("SELECT 1 FROM properties WHERE id = :property_id LIMIT 1"),
            {"property_id": property_id},
        ).first()
        if not exists:
            raise HTTPException(status_code=404, detail=f"Property id={property_id} not found")

        # Create table + unique index (for upsert)
        db.execute(text(CREATE_TABLE_SQL))
        db.execute(text(ENSURE_UNIQUE_SQL))

        # Upsert sample rows (now 8 guides)
        db.execute(text(UPSERT_GUIDES_SQL), {"property_id": property_id})
        db.commit()

        return {
            "status": "ok",
            "message": f"Guides table ready and 8 sample guides upserted for property_id={property_id}",
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
