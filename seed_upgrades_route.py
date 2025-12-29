# seed_upgrades_route.py
"""
Browser-accessible endpoint to:
1) Create the 'upgrades' table if missing
2) Upsert sample upgrades for a chosen property_id (idempotent)

Visit:
    https://hostaway-casaseaesta-api.onrender.com/seed-upgrades?property_id=2
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db

router = APIRouter()

# --- Create table (aligned with typical structure; adjust if your real schema differs) ---
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS upgrades (
    id SERIAL PRIMARY KEY,
    property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,

    title TEXT NOT NULL,
    slug TEXT NOT NULL,

    short_description TEXT,
    long_description TEXT,

    price_cents INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'usd',

    is_active BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,

    stripe_price_id TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

# Unique index for idempotent upserts
ENSURE_UNIQUE_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND indexname = 'ux_upgrades_property_slug'
    ) THEN
        CREATE UNIQUE INDEX ux_upgrades_property_slug ON upgrades (property_id, slug);
    END IF;
END $$;
"""

UPSERT_UPGRADES_SQL = """
INSERT INTO upgrades (
    property_id, title, slug,
    short_description, long_description,
    price_cents, currency,
    is_active, sort_order,
    stripe_price_id,
    updated_at
)
VALUES
(
    :property_id,
    'Early Check-in',
    'early-check-in',
    'Arrive early and start relaxing sooner.',
    'Arrive early and start relaxing sooner with guaranteed early access to the property.',
    3500,
    'usd',
    TRUE,
    1,
    NULL,
    NOW()
),
(
    :property_id,
    'Purchase Groceries',
    'groceries',
    'Let us stock the fridge before you arrive.',
    'Send us your list and we''ll have your favorite groceries ready and waiting when you arrive.',
    6000,
    'usd',
    TRUE,
    2,
    NULL,
    NOW()
),
(
    :property_id,
    'Mid-Stay Clean',
    'mid-stay-clean',
    'A fresh clean during your stay.',
    'Enjoy fresh towels, linens, and a tidy space with a full clean during your stay.',
    8500,
    'usd',
    TRUE,
    3,
    NULL,
    NOW()
),
(
    :property_id,
    'Late Checkout',
    'late-checkout',
    'Enjoy a slower, more relaxed departure.',
    'Extend your stay a few extra hours so you can pack up and head out at your own pace.',
    3000,
    'usd',
    TRUE,
    4,
    NULL,
    NOW()
)
ON CONFLICT (property_id, slug) DO UPDATE SET
    title = EXCLUDED.title,
    short_description = EXCLUDED.short_description,
    long_description = EXCLUDED.long_description,
    price_cents = EXCLUDED.price_cents,
    currency = EXCLUDED.currency,
    is_active = EXCLUDED.is_active,
    sort_order = EXCLUDED.sort_order,
    stripe_price_id = EXCLUDED.stripe_price_id,
    updated_at = NOW();
"""


@router.get("/seed-upgrades")
def seed_upgrades(
    property_id: int = Query(..., description="ID of the property to attach upgrades to"),
    db: Session = Depends(get_db),
):
    """
    Visit in browser:
        /seed-upgrades?property_id=1
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

        # Upsert sample rows
        db.execute(text(UPSERT_UPGRADES_SQL), {"property_id": property_id})
        db.commit()

        return {
            "status": "ok",
            "message": f"Upgrades table ready and sample upgrades upserted for property_id={property_id}",
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
