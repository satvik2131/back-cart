"""Data access for products.

Read-only for this module — no mutation endpoints exist yet. ``get_product``
returns ``None`` only to mean "no such product"; callers that need a hard
failure use :func:`app.features.products.dependencies.get_product_or_404`.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.products.models import Product


async def list_products(session: AsyncSession) -> Sequence[Product]:
    result = await session.execute(select(Product).order_by(Product.name))
    return result.scalars().all()


async def get_product(
    session: AsyncSession, product_id: uuid.UUID
) -> Product | None:
    return await session.get(Product, product_id)
