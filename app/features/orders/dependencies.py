import uuid

from fastapi import Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.session import get_session
from app.features.orders import service
from app.features.orders.schemas import OrderRead


async def get_order_or_404(
    order_id: uuid.UUID = Path(..., description="Order id"),
    session: AsyncSession = Depends(get_session),
) -> OrderRead:
    """Resolve a path ``order_id`` to its (snapshotted) ``OrderRead`` or 404."""
    order = await service.get_order(session, order_id)
    if order is None:
        raise NotFoundError(
            f"Order {order_id} does not exist.",
            details={"order_id": str(order_id)},
        )
    return order
