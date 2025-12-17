# seed_upgrades.py
"""
Idempotent upgrade seeder that stays resilient to DB schema changes.

What it does:
- Validates the property exists in `properties`
- Reflects the actual `upgrades` table columns from the DB (so it won't break if models drift)
- Ensures a unique index on (property_id, slug) for safe upserts
- Upserts the sample upgrades (re-run friendly)

Usage:
  python seed_upgrades.py --property-id 5
or:
  PROPERTY_ID=5 python seed_upgrades.py
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict, Optional

from sqlalchemy import MetaData, Table, inspect, text
from sqlalchemy.orm import Session

from database import SessionLocal

DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "usd")

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


def _property_exists(db: Session, property_id: int) -> bool:
    return bool(
        db.execute(
            text("SELECT 1 FROM properties WHERE id = :pid LIMIT 1"),
            {"pid": property_id},
        ).first()
    )


def _reflect_table(db: Session, table_name: str) -> Table:
    engine = db.get_bind()
    md = MetaData()
    return Table(table_name, md, autoload_with=engine)


def _ensure_unique_index(db: Session) -> None:
    """
    Create a unique index on (property_id, slug) if it doesn't exist.
    Used for ON CONFLICT upserts.
    """
    db.execute(
        text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND indexname = 'ux_upgrades_property_slug'
                ) THEN
                    CREATE UNIQUE INDEX ux_upgrades_property_slug
                    ON upgrades (property_id, slug);
                END IF;
            END $$;
            """
        )
    )


def _pick_price_column(columns: set[str]) -> Optional[str]:
    # Common variants we’ve seen in apps
    candidates = [
        "price_cents",
        "amount_cents",
        "price_amount_cents",
        "price_in_cents",
    ]
    for c in candidates:
        if c in columns:
            return c
    return None


def _build_row(
    property_id: int,
    upgrade: Dict[str, Any],
    sort_order: int,
    table_columns: set[str],
) -> Dict[str, Any]:
    row: Dict[str, Any] = {}

    # Always needed
    if "property_id" in table_columns:
        row["property_id"] = property_id
    if "slug" in table_columns:
        row["slug"] = upgrade["slug"]
    if "title" in table_columns:
        row["title"] = upgrade["title"]

    # Descriptions (handle a couple likely renames)
    if "short_description" in table_columns:
        row["short_description"] = upgrade.get("short_description")
    elif "description" in table_columns:
        row["description"] = upgrade.get("short_description")

    if "long_description" in table_columns:
        row["long_description"] = upgrade.get("long_description")
    elif "details" in table_columns:
        row["details"] = upgrade.get("long_description")

    # Pricing
    price_col = _pick_price_column(table_columns)
    if price_col:
        row[price_col] = upgrade.get("price_cents")

    # Other common fields
    if "currency" in table_columns:
        row["currency"] = DEFAULT_CURRENCY
    if "is_active" in table_columns:
        row["is_active"] = True
    if "sort_order" in table_columns:
        row["sort_order"] = sort_order
    if "stripe_price_id" in table_columns:
        row["stripe_price_id"] = None

    # Timestamps (don’t set if your DB handles defaults/triggers, but safe if present)
    if "updated_at" in table_columns:
        row["updated_at"] = text("NOW()")
    if "created_at" in table_columns:
        # only set on insert via COALESCE in upsert below if desired
        # but leaving it out respects DEFAULT NOW()
        pass

    return row


def run(property_id: int) -> None:
    db: Session = SessionLocal()
    try:
        if not _property_exists(db, property_id):
            raise SystemExit(f"❌ Property id={property_id} not found in properties table")

        inspector = inspect(db.get_bind())
        if "upgrades" not in inspector.get_table_names():
            raise SystemExit("❌ Table 'upgrades' does not exist (run migrations first)")

        upgrades_table = _reflect_table(db, "upgrades")
        table_columns = {c.name for c in upgrades_table.columns}

        # Ensure unique index so upserts work
        _ensure_unique_index(db)

        # Build one upsert per row (keeps it simple + clear)
        for idx, u in enumerate(UPGRADES, start=1):
            row = _build_row(property_id, u, idx, table_columns)

            # Build SET clause for update (exclude conflict keys)
            update_cols = dict(row)
            update_cols.pop("property_id", None)
            update_cols.pop("slug", None)

            # If your table has updated_at, force it on update even if not in row
            if "updated_at" in table_columns:
                update_cols["updated_at"] = text("NOW()")

            stmt = (
                upgrades_table.insert()
                .values(**row)
                .on_conflict_do_update(
                    index_elements=[upgrades_table.c.property_id, upgrades_table.c.slug],
                    set_=update_cols,
                )
            )

            db.execute(stmt)
            print(f"Upserted upgrade → {u['title']}")

        db.commit()
        print("✅ Done seeding upgrades!")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--property-id",
        type=int,
        default=int(os.getenv("PROPERTY_ID", "5")),
        help="Property ID to seed upgrades for (default: env PROPERTY_ID or 5)",
    )
    args = parser.parse_args()
    run(args.property_id)
