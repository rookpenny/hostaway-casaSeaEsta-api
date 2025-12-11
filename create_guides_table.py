# create_guides_table.py
"""
Creates the 'guides' table and inserts sample guide records.
Run this file once:

    python create_guides_table.py

Requires: database.py (engine), models.py (Guide model)
"""

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from database import engine
from models import Guide
from datetime import datetime

# -----------------------------------------
# 1) CREATE TABLE (raw SQL, safe to run once)
# -----------------------------------------
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS guides (
    id SERIAL PRIMARY KEY,
    property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,

    title VARCHAR NOT NULL,
    category VARCHAR,
    short_description VARCHAR,
    long_description TEXT,
    body_html TEXT,
    image_url VARCHAR,

    is_published BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

# -----------------------------------------
# 2) INSERT SAMPLE ROWS
# -----------------------------------------
INSERT_SAMPLE_GUIDES_SQL = """
INSERT INTO guides (
    property_id,
    title,
    category,
    short_description,
    long_description,
    body_html,
    image_url,
    is_published,
    sort_order
)
VALUES
(
    :property_id,
    'Best Coffee Within 10 Minutes',
    'Food & Drink',
    'Three great spots for espresso, cold brew, and pastries.',
    'Here are three local coffee shops we recommend:

1) Shoreline Coffee – 5 min walk
2) Marina Roasters – 8 min drive
3) Harbor Bakery – 10 min walk with amazing croissants.',
    NULL,
    'https://placehold.co/600x400',
    TRUE,
    1
),
(
    :property_id,
    'Kid-Friendly Afternoon Nearby',
    'Family',
    'Playground + ice cream combo the kids will love.',
    'If you''re traveling with kids, this is our go-to afternoon plan:

- Start at Harbor Park playground
- Walk over to Scoops & Smiles ice cream
- Optional stop at the pier for sunset.',
    NULL,
    'https://placehold.co/600x400',
    TRUE,
    2
);
"""

# -----------------------------------------
# RUN MIGRATION + SEED
# -----------------------------------------
def main():
    property_id = int(input("Enter property_id to seed guides for: "))

    with engine.begin() as conn:
        print("🔧 Creating guides table (if not exists)...")
        conn.execute(text(CREATE_TABLE_SQL))

        print(f"📌 Inserting sample guides for property_id={property_id} ...")
        conn.execute(text(INSERT_SAMPLE_GUIDES_SQL), {"property_id": property_id})

    print("✅ Done! Guides table ready and sample data inserted.")
    print("Now visit:")
    print(f"   /properties/{property_id}/guides")
    print("Or open the app and navigate to:  Guides → it should now show cards.")

if __name__ == "__main__":
    main()
