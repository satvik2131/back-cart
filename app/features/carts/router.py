import uuid

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorEnvelope
from app.db.session import get_session
from app.features.carts import service
from app.features.carts.dependencies import get_cart_or_404, get_open_cart_for_update
from app.features.carts.models import Cart
from app.features.carts.schemas import (
    CartItemCreate,
    CartItemRead,
    CartItemUpdate,
    CartRead,
)

router = APIRouter(prefix="/carts", tags=["carts"])

_NOT_FOUND = {404: {"model": ErrorEnvelope, "description": "Cart or item not found"}}
_CONFLICT = {
    409: {
        "model": ErrorEnvelope,
        "description": "Cart already checked out, or not enough inventory",
    }
}
_VALIDATION = {422: {"model": ErrorEnvelope, "description": "Invalid request body"}}


async def _cart_read(session: AsyncSession, cart: Cart) -> CartRead:
    """Assemble a CartRead with prices/totals computed from current products."""
    pairs = await service.list_items_with_products(session, cart.id)
    items = [
        CartItemRead(
            id=item.id,
            product_id=product.id,
            product_name=product.name,
            unit_price_cents=product.unit_price_cents,
            quantity=item.quantity,
            line_subtotal_cents=product.unit_price_cents * item.quantity,
        )
        for item, product in pairs
    ]
    return CartRead(
        id=cart.id,
        status=cart.status,
        items=items,
        total_cents=sum(line.line_subtotal_cents for line in items),
        created_at=cart.created_at,
        updated_at=cart.updated_at,
    )


@router.post("", response_model=CartRead, status_code=status.HTTP_201_CREATED)
async def create_cart(
    session: AsyncSession = Depends(get_session),
) -> CartRead:
    cart = await service.create_cart(session)
    await session.commit()
    return await _cart_read(session, cart)


@router.get("/{cart_id}", response_model=CartRead, responses={**_NOT_FOUND})
async def get_cart(
    cart: Cart = Depends(get_cart_or_404),
    session: AsyncSession = Depends(get_session),
) -> CartRead:
    return await _cart_read(session, cart)


@router.post(
    "/{cart_id}/items",
    response_model=CartRead,
    status_code=status.HTTP_201_CREATED,
    responses={**_NOT_FOUND, **_CONFLICT, **_VALIDATION},
)
async def add_cart_item(
    payload: CartItemCreate,
    cart: Cart = Depends(get_open_cart_for_update),
    session: AsyncSession = Depends(get_session),
) -> CartRead:
    product = await service.require_product(session, payload.product_id)
    await service.add_item(session, cart, product, payload.quantity)
    await session.commit()
    return await _cart_read(session, cart)


@router.patch(
    "/{cart_id}/items/{item_id}",
    response_model=CartRead,
    responses={**_NOT_FOUND, **_CONFLICT, **_VALIDATION},
)
async def update_cart_item(
    payload: CartItemUpdate,
    item_id: uuid.UUID = Path(..., description="Cart item id"),
    cart: Cart = Depends(get_open_cart_for_update),
    session: AsyncSession = Depends(get_session),
) -> CartRead:
    if payload.quantity == 0:
        await service.remove_item(session, cart, item_id)
    else:
        await service.update_item_quantity(
            session, cart, item_id, payload.quantity
        )
    await session.commit()
    return await _cart_read(session, cart)


@router.delete(
    "/{cart_id}/items/{item_id}",
    response_model=CartRead,
    responses={**_NOT_FOUND, **_CONFLICT},
)
async def delete_cart_item(
    item_id: uuid.UUID = Path(..., description="Cart item id"),
    cart: Cart = Depends(get_open_cart_for_update),
    session: AsyncSession = Depends(get_session),
) -> CartRead:
    await service.remove_item(session, cart, item_id)
    await session.commit()
    return await _cart_read(session, cart)
