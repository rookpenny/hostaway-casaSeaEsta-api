# seed_guides_route.py
"""
Browser-accessible endpoint to:
1. Create the 'guides' table if missing
2. Insert sample guides for a chosen property_id

Visit:
    /seed-guides?property_id=1

Example:
    https://your-api.com/seed-guides?property_id=1
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
from database import engine, get_db

router = APIRouter()

# --- SQL for creating table ---
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS guides (
    id SERIAL PRIMARY KEY,
    property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,

    slug VARCHAR,
    title VARCHAR NOT NULL,
    category VARCHAR,
    short_description VARCHAR,
    long_description TEXT,
    body_html TEXT,
    image_url VARCHAR,

    is_active BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

# --- Insert sample data ---
INSERT_GUIDES_SQL = """
INSERT INTO guides (
    property_id, slug, title, category,
    short_description, long_description, image_url,
    is_active, sort_order
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
    1
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
    2
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
    3
);
"""


@router.get("/seed-guides")
def seed_guides(
    property_id: int = Query(..., description="ID of the property to attach guides to"),
):
    """
    Visit in browser:
        /seed-guides?property_id=1
    """
    try:
        # Create table
        with engine.begin() as conn:
            conn.execute(text(CREATE_TABLE_SQL))

        # Insert sample rows
        with engine.begin() as conn:
            conn.execute(text(INSERT_GUIDES_SQL), {"property_id": property_id})

        return {
            "status": "ok",
            "message": f"Guides table created and 3 sample guides inserted for property_id={property_id}"
        }

    except Exception as e:
        return {"status": "error", "detail": str(e)}
