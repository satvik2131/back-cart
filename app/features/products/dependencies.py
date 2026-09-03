import uuid

from fastapi import Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.session import get_session
from app.features.products import service
from app.features.products.models import Product


async def get_product_or_404(
    product_id: uuid.UUID = Path(..., description="Product id"),
    session: AsyncSession = Depends(get_session),
) -> Product:
    """Resolve a path ``product_id`` to a ``Product`` or raise a 404 envelope."""
    product = await service.get_product(session, product_id)
    if product is None:
        raise NotFoundError(
            f"Product {product_id} does not exist.",
            details={"product_id": str(product_id)},
        )
    return product
