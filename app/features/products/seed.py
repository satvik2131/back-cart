"""Idempotent product seed data.

Run inside the api container::

    docker compose exec api python -m app.features.products.seed
    # or: make seed

Inserts the catalogue below, skipping any product whose ``name`` already
exists, so it is safe to run repeatedly. The catalogue deliberately includes
one low-inventory product (2 units) and one out-of-stock product (0 units) so
oversell and out-of-stock paths can be exercised by later modules.

This is a standalone script, not an Alembic data migration — see DECISIONS.md
§3 "Seed Data as a Standalone Script".
"""

import asyncio

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.features.products.models import Product

SEED_PRODUCTS: list[dict[str, object]] = [
    {"name": "Standard Ceramic Mug", "unit_price_cents": 1200, "inventory": 100},
    {"name": "Insulated Steel Bottle", "unit_price_cents": 2500, "inventory": 40},
    {"name": "Cotton Tote Bag", "unit_price_cents": 900, "inventory": 250},
    {"name": "Enamel Camp Plate", "unit_price_cents": 1800, "inventory": 15},
    {"name": "Limited Edition Notebook", "unit_price_cents": 3499, "inventory": 2},
    {"name": "Collector Pin (Sold Out)", "unit_price_cents": 750, "inventory": 0},
]


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        existing_names = set(
            (await session.execute(select(Product.name))).scalars().all()
        )
        added = 0
        for row in SEED_PRODUCTS:
            if row["name"] in existing_names:
                continue
            session.add(Product(**row))
            added += 1
        await session.commit()

    print(
        f"Seeded {added} new product(s); "
        f"{len(existing_names)} already present, {len(SEED_PRODUCTS)} in catalogue."
    )


if __name__ == "__main__":
    asyncio.run(seed())
