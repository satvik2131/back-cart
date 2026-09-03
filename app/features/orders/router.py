import uuid

from fastapi import APIRouter, Depends, Header, Path, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorEnvelope
from app.db.session import get_session
from app.features.orders import service
from app.features.orders.dependencies import get_order_or_404
from app.features.orders.schemas import CheckoutRequest, OrderRead

router = APIRouter(tags=["checkout / orders"])

_ENVELOPE = {"model": ErrorEnvelope}


@router.post(
    "/carts/{cart_id}/checkout",
    response_model=OrderRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {**_ENVELOPE, "description": "Cart not found"},
        409: {
            **_ENVELOPE,
            "description": "Cart already checked out, not enough inventory, "
            "or idempotency key reused for another cart",
        },
        422: {
            **_ENVELOPE,
            "description": "Missing Idempotency-Key header, or empty cart",
        },
    },
)
async def checkout(
    cart_id: uuid.UUID = Path(..., description="Cart id"),
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        description="Client-generated key; reuse it verbatim on retry.",
    ),
    _body: CheckoutRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    try:
        body, status_code = await service.checkout(
            session, cart_id, idempotency_key
        )
        await session.commit()
    except Exception:
        # Any failure inside the checkout transaction unwinds every step —
        # inventory decrements, the order, the cart status — so a retry with the
        # same key starts from a clean slate.
        await session.rollback()
        raise
    return JSONResponse(status_code=status_code, content=body)


@router.get(
    "/orders/{order_id}",
    response_model=OrderRead,
    responses={404: {**_ENVELOPE, "description": "Order not found"}},
)
async def get_order(order: OrderRead = Depends(get_order_or_404)) -> OrderRead:
    return order
