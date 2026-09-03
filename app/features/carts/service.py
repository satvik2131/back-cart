"""Cart data access and mutation logic.

Concurrency model (see DECISIONS.md §3 "Cart-Row Lock Serializes Cart
Mutations"): every mutating operation first takes a row lock on the cart via
:func:`get_open_cart_for_update`, so concurrent add/update/remove calls against
the same cart run one at a time. The ``(cart_id, product_id)`` unique constraint
and the ``quantity > 0`` CHECK are the structural backstops.

The add-time inventory check here is *soft* — a best-effort guard that does not
reserve stock. The authoritative check is an atomic conditional decrement inside
the checkout transaction (a later module). See DECISIONS.md §3 "Soft Inventory
Check at Add-Time".
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    CartAlreadyCheckedOutError,
    InsufficientInventoryError,
    NotFoundError,
)
from app.features.carts.models import Cart, CartItem, CartStatus
from app.features.products import service as products_service
from app.features.products.models import Product


async def create_cart(session: AsyncSession) -> Cart:
    cart = Cart(status=CartStatus.OPEN)
    session.add(cart)
    await session.flush()
    await session.refresh(cart)
    return cart


async def get_cart(session: AsyncSession, cart_id: uuid.UUID) -> Cart | None:
    return await session.get(Cart, cart_id)


async def get_open_cart_for_update(
    session: AsyncSession, cart_id: uuid.UUID
) -> Cart:
    """Row-lock a cart for a mutation.

    Raises ``NOT_FOUND`` if the cart does not exist and
    ``CART_ALREADY_CHECKED_OUT`` if it is no longer open. The lock is held for
    the rest of the request's transaction.
    """
    cart = await session.get(Cart, cart_id, with_for_update=True)
    if cart is None:
        raise NotFoundError(
            f"Cart {cart_id} does not exist.",
            details={"cart_id": str(cart_id)},
        )
    if cart.status is not CartStatus.OPEN:
        raise CartAlreadyCheckedOutError(
            f"Cart {cart_id} is already checked out and cannot be modified.",
            details={"cart_id": str(cart_id), "status": cart.status.value},
        )
    return cart


async def require_product(
    session: AsyncSession, product_id: uuid.UUID
) -> Product:
    product = await products_service.get_product(session, product_id)
    if product is None:
        raise NotFoundError(
            f"Product {product_id} does not exist.",
            details={"product_id": str(product_id)},
        )
    return product


async def list_items_with_products(
    session: AsyncSession, cart_id: uuid.UUID
) -> Sequence[Row[tuple[CartItem, Product]]]:
    """Cart lines joined to their products, for computing prices/totals."""
    stmt = (
        select(CartItem, Product)
        .join(Product, CartItem.product_id == Product.id)
        .where(CartItem.cart_id == cart_id)
        .order_by(Product.name)
    )
    result = await session.execute(stmt)
    return result.all()


async def _get_item_for_cart(
    session: AsyncSession, cart_id: uuid.UUID, item_id: uuid.UUID
) -> CartItem:
    item = await session.get(CartItem, item_id)
    if item is None or item.cart_id != cart_id:
        raise NotFoundError(
            f"Cart item {item_id} does not exist in cart {cart_id}.",
            details={"cart_id": str(cart_id), "item_id": str(item_id)},
        )
    return item


def _soft_inventory_check(product: Product, requested_quantity: int) -> None:
    if requested_quantity > product.inventory:
        raise InsufficientInventoryError(
            f"Only {product.inventory} unit(s) of {product.name} are available.",
            details={
                "product_id": str(product.id),
                "requested": requested_quantity,
                "available": product.inventory,
            },
        )


async def add_item(
    session: AsyncSession, cart: Cart, product: Product, quantity: int
) -> CartItem:
    """Add ``quantity`` of ``product`` to ``cart``, incrementing an existing
    line rather than creating a duplicate. Caller must hold the cart lock."""
    existing = await session.scalar(
        select(CartItem).where(
            CartItem.cart_id == cart.id, CartItem.product_id == product.id
        )
    )
    resulting_quantity = quantity + (existing.quantity if existing else 0)
    _soft_inventory_check(product, resulting_quantity)

    if existing is None:
        item = CartItem(
            cart_id=cart.id, product_id=product.id, quantity=quantity
        )
        session.add(item)
    else:
        existing.quantity = resulting_quantity
        item = existing

    await session.flush()
    await session.refresh(item)
    return item


async def update_item_quantity(
    session: AsyncSession,
    cart: Cart,
    item_id: uuid.UUID,
    quantity: int,
) -> CartItem:
    """Set a line's absolute quantity. ``quantity`` must be > 0 — the router
    routes 0 to :func:`remove_item`. Caller must hold the cart lock."""
    item = await _get_item_for_cart(session, cart.id, item_id)
    product = await require_product(session, item.product_id)
    _soft_inventory_check(product, quantity)
    item.quantity = quantity
    await session.flush()
    await session.refresh(item)
    return item


async def remove_item(
    session: AsyncSession, cart: Cart, item_id: uuid.UUID
) -> None:
    """Remove a line from the cart. Caller must hold the cart lock."""
    item = await _get_item_for_cart(session, cart.id, item_id)
    await session.delete(item)
    await session.flush()
