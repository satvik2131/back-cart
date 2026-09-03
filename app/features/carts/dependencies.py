import uuid

from fastapi import Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.session import get_session
from app.features.carts import service
from app.features.carts.models import Cart


async def get_cart_or_404(
    cart_id: uuid.UUID = Path(..., description="Cart id"),
    session: AsyncSession = Depends(get_session),
) -> Cart:
    """Resolve a path ``cart_id`` to a ``Cart`` (read, no lock) or raise 404."""
    cart = await service.get_cart(session, cart_id)
    if cart is None:
        raise NotFoundError(
            f"Cart {cart_id} does not exist.",
            details={"cart_id": str(cart_id)},
        )
    return cart


async def get_open_cart_for_update(
    cart_id: uuid.UUID = Path(..., description="Cart id"),
    session: AsyncSession = Depends(get_session),
) -> Cart:
    """Row-lock an open cart for a mutating endpoint.

    404 if missing, ``CART_ALREADY_CHECKED_OUT`` (409) if not open. The lock is
    held until the request transaction ends, so it also stabilises the status
    check and serialises concurrent mutations of the same cart.
    """
    return await service.get_open_cart_for_update(session, cart_id)
