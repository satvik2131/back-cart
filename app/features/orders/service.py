"""Checkout and order retrieval.

The checkout flow is one database transaction (opened by the request's session,
committed by the router). Its structure and the reasoning behind each guard are
in DECISIONS.md §3 ("Checkout Transaction Boundary", "Idempotency Strategy for
Checkout", "Concurrency Control for Inventory Deduction") and §4.

Concurrency safety comes entirely from the database:
* the cart row is locked ``FOR UPDATE`` for the whole transaction, so the
  open/checked-out check cannot go stale and two checkouts of the same cart
  serialise;
* inventory is decremented with an atomic conditional ``UPDATE ... WHERE
  inventory >= :qty``; zero rows affected means "not enough stock" and aborts
  the transaction, so nothing is ever partially decremented;
* ``idempotency_keys.key`` is a primary key — a replayed request finds the
  stored response instead of doing the work again.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    CartAlreadyCheckedOutError,
    EmptyCartError,
    IdempotencyKeyReuseError,
    InsufficientInventoryError,
    NotFoundError,
)
from app.features.carts.models import Cart, CartItem, CartStatus
from app.features.coupons import service as coupons_service
from app.features.orders.models import IdempotencyKey, Order, OrderItem, OrderStatus
from app.features.orders.schemas import OrderItemRead, OrderRead
from app.features.products.models import Product

CheckoutResult = tuple[dict, int]


def _order_read(order: Order, items: Sequence[OrderItem]) -> OrderRead:
    return OrderRead(
        id=order.id,
        cart_id=order.cart_id,
        status=order.status,
        items=[
            OrderItemRead(
                id=it.id,
                product_id=it.product_id,
                product_name=it.product_name_snapshot,
                unit_price_cents=it.unit_price_cents_snapshot,
                quantity=it.quantity,
                line_total_cents=it.line_total_cents,
            )
            for it in items
        ],
        gross_total_cents=order.gross_total_cents,
        discount_cents=order.discount_cents,
        net_total_cents=order.net_total_cents,
        created_at=order.created_at,
    )


def _response_body(order: Order, items: Sequence[OrderItem]) -> dict:
    """The exact JSON stored for idempotent replay and returned to the client."""
    return _order_read(order, items).model_dump(mode="json")


async def _stored_replay(
    session: AsyncSession, key: str, cart_id: uuid.UUID
) -> CheckoutResult | None:
    record = await session.get(IdempotencyKey, key)
    if record is None:
        return None
    if record.cart_id != cart_id:
        raise IdempotencyKeyReuseError(
            "This Idempotency-Key was already used for a different cart.",
            details={"idempotency_key": key, "cart_id": str(cart_id)},
        )
    return record.response_body, record.status_code


async def _cart_items_with_products(
    session: AsyncSession, cart_id: uuid.UUID
) -> Sequence[Row[tuple[CartItem, Product]]]:
    stmt = (
        select(CartItem, Product)
        .join(Product, CartItem.product_id == Product.id)
        .where(CartItem.cart_id == cart_id)
        .order_by(Product.name)
    )
    return (await session.execute(stmt)).all()


async def checkout(
    session: AsyncSession,
    cart_id: uuid.UUID,
    idempotency_key: str,
    coupon_code: str | None = None,
) -> CheckoutResult:
    # Fast path: this key was already processed — return the stored response
    # verbatim without touching the cart.
    replay = await _stored_replay(session, idempotency_key, cart_id)
    if replay is not None:
        return replay

    # Lock the cart for the whole transaction.
    cart = await session.get(Cart, cart_id, with_for_update=True)
    if cart is None:
        raise NotFoundError(
            f"Cart {cart_id} does not exist.",
            details={"cart_id": str(cart_id)},
        )

    if cart.status is not CartStatus.OPEN:
        # A concurrent request with the same key may have just finished while we
        # waited for the lock — prefer replaying its result over a 409.
        replay = await _stored_replay(session, idempotency_key, cart_id)
        if replay is not None:
            return replay
        raise CartAlreadyCheckedOutError(
            f"Cart {cart_id} is already checked out.",
            details={"cart_id": str(cart_id), "status": cart.status.value},
        )

    rows = await _cart_items_with_products(session, cart_id)
    if not rows:
        raise EmptyCartError(
            f"Cart {cart_id} has no items to check out.",
            details={"cart_id": str(cart_id)},
        )

    # (b) Atomic conditional inventory decrement, one product at a time. Any
    # failure raises and the whole transaction (including earlier decrements)
    # rolls back.
    for cart_item, product in rows:
        result = await session.execute(
            update(Product)
            .where(
                Product.id == product.id,
                Product.inventory >= cart_item.quantity,
            )
            .values(inventory=Product.inventory - cart_item.quantity)
        )
        if result.rowcount != 1:
            available = await session.scalar(
                select(Product.inventory).where(Product.id == product.id)
            )
            raise InsufficientInventoryError(
                f"Only {available} unit(s) of {product.name} are available, "
                f"{cart_item.quantity} requested.",
                details={
                    "product_id": str(product.id),
                    "product_name": product.name,
                    "requested": cart_item.quantity,
                    "available": available,
                },
            )

    # (c-e) Totals from the CURRENT product price (matches the Carts module's
    # "cart items hold no price" decision).
    gross_total_cents = sum(
        product.unit_price_cents * cart_item.quantity
        for cart_item, product in rows
    )

    # Coupon validation + discount quote happen AFTER inventory has succeeded,
    # so a checkout doomed by stock never touches a coupon (invariant 6). No
    # coupon state is changed yet — only quoted.
    coupon = None
    discount_cents = 0
    if coupon_code:
        coupon, discount_cents = await coupons_service.quote_discount(
            session, coupon_code, gross_total_cents
        )

    net_total_cents = max(0, gross_total_cents - discount_cents)

    # (f) Order + fully snapshotted items.
    order = Order(
        cart_id=cart_id,
        status=OrderStatus.SUCCESS,
        gross_total_cents=gross_total_cents,
        discount_cents=discount_cents,
        net_total_cents=net_total_cents,
    )
    session.add(order)
    await session.flush()

    # Now the order id exists: atomically redeem the coupon against it. If a
    # parallel checkout won the race, this raises and the whole transaction —
    # including the inventory decrements above — rolls back.
    if coupon is not None:
        await coupons_service.mark_redeemed(session, coupon, order.id)

    order_items = [
        OrderItem(
            order_id=order.id,
            product_id=product.id,
            product_name_snapshot=product.name,
            unit_price_cents_snapshot=product.unit_price_cents,
            quantity=cart_item.quantity,
            line_total_cents=product.unit_price_cents * cart_item.quantity,
        )
        for cart_item, product in rows
    ]
    session.add_all(order_items)

    # (g) Close the cart.
    cart.status = CartStatus.CHECKED_OUT

    await session.flush()

    # (h) Store the response for idempotent replay.
    body = _response_body(order, order_items)
    session.add(
        IdempotencyKey(
            key=idempotency_key,
            cart_id=cart_id,
            response_body=body,
            status_code=201,
        )
    )
    await session.flush()

    return body, 201


async def get_order(
    session: AsyncSession, order_id: uuid.UUID
) -> OrderRead | None:
    order = await session.get(Order, order_id)
    if order is None:
        return None
    items = (
        await session.execute(
            select(OrderItem)
            .where(OrderItem.order_id == order_id)
            .order_by(OrderItem.product_name_snapshot)
        )
    ).scalars().all()
    return _order_read(order, items)
