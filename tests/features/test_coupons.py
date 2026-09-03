import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.features.carts.models import Cart, CartStatus
from app.features.coupons.models import Coupon, CouponStatus
from app.features.orders.models import Order, OrderStatus
from app.features.products.models import Product
from tests.conftest import clear_domain

pytestmark = pytest.mark.asyncio

EVERY = settings.COUPON_MILESTONE_EVERY
PERCENT = settings.COUPON_DISCOUNT_PERCENT


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


async def _make_successful_orders(session: AsyncSession, count: int) -> None:
    """Insert `count` checked-out carts + successful orders (totals are dummy)."""
    for _ in range(count):
        cart = Cart(status=CartStatus.CHECKED_OUT)
        session.add(cart)
        await session.flush()
        session.add(
            Order(
                cart_id=cart.id,
                status=OrderStatus.SUCCESS,
                gross_total_cents=1000,
                discount_cents=0,
                net_total_cents=1000,
            )
        )
    await session.flush()


@pytest_asyncio.fixture
async def widget(db_session: AsyncSession) -> Product:
    await clear_domain(db_session)
    product = Product(name="Widget", unit_price_cents=1000, inventory=100)
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    return product


async def _cart_with(client, product: Product, quantity: int) -> str:
    cart_id = (await client.post("/carts")).json()["id"]
    await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(product.id), "quantity": quantity},
    )
    return cart_id


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #


async def test_generate_before_milestone_returns_422(client, widget):
    # widget fixture cleared all domain tables -> zero successful orders
    resp = await client.post("/admin/coupons/generate")

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "MILESTONE_NOT_REACHED"


async def test_generate_at_milestone_succeeds(client, widget, db_session):
    await _make_successful_orders(db_session, EVERY)

    resp = await client.post("/admin/coupons/generate")

    assert resp.status_code == 201
    body = resp.json()
    assert body["milestone_number"] == EVERY
    assert body["discount_percent"] == PERCENT
    assert body["status"] == "available"
    assert body["code"].startswith(f"SAVE{PERCENT}-")
    assert body["redeemed_at"] is None


async def test_generate_twice_for_same_milestone_returns_409(
    client, widget, db_session
):
    await _make_successful_orders(db_session, EVERY)

    assert (await client.post("/admin/coupons/generate")).status_code == 201
    resp = await client.post("/admin/coupons/generate")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "MILESTONE_ALREADY_REWARDED"


async def test_concurrent_generate_creates_one_coupon(
    committing_client, committing_session: AsyncSession, tracked
):
    await _make_successful_orders(committing_session, EVERY)
    await committing_session.commit()
    carts = (
        await committing_session.execute(select(Cart.id))
    ).scalars().all()
    tracked.cart_ids.extend(carts)

    r1, r2 = await asyncio.gather(
        committing_client.post("/admin/coupons/generate"),
        committing_client.post("/admin/coupons/generate"),
    )

    coupons = (
        await committing_session.execute(select(Coupon))
    ).scalars().all()
    tracked.coupon_ids.extend(c.id for c in coupons)

    assert sorted([r1.status_code, r2.status_code]) == [201, 409]
    assert len(coupons) == 1
    assert coupons[0].milestone_number == EVERY


# --------------------------------------------------------------------------- #
# redemption at checkout
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def available_coupon(db_session: AsyncSession) -> Coupon:
    coupon = Coupon(
        milestone_number=EVERY,
        discount_percent=PERCENT,
        code="SAVE-TEST",
        status=CouponStatus.AVAILABLE,
    )
    db_session.add(coupon)
    await db_session.flush()
    await db_session.refresh(coupon)
    return coupon


async def test_valid_coupon_applies_discount_and_redeems(
    client, widget, available_coupon, db_session
):
    cart_id = await _cart_with(client, widget, 5)  # gross 5000

    resp = await client.post(
        f"/carts/{cart_id}/checkout",
        headers={"Idempotency-Key": "c-1"},
        json={"coupon_code": "SAVE-TEST"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["gross_total_cents"] == 5000
    assert body["discount_cents"] == 5000 * PERCENT // 100
    assert body["net_total_cents"] == 5000 - body["discount_cents"]

    await db_session.refresh(available_coupon)
    assert available_coupon.status is CouponStatus.REDEEMED
    assert str(available_coupon.redeemed_by_order_id) == body["id"]
    assert available_coupon.redeemed_at is not None


async def test_invalid_coupon_code_returns_422(client, widget):
    cart_id = await _cart_with(client, widget, 1)

    resp = await client.post(
        f"/carts/{cart_id}/checkout",
        headers={"Idempotency-Key": "c-2"},
        json={"coupon_code": "NOPE"},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_COUPON"


async def test_already_redeemed_coupon_fails_and_creates_no_order(
    client, widget, available_coupon, db_session
):
    # tie it to some order so the status/redeemed_* consistency CHECK holds
    other_cart = Cart(status=CartStatus.CHECKED_OUT)
    db_session.add(other_cart)
    await db_session.flush()
    other_order = Order(
        cart_id=other_cart.id,
        status=OrderStatus.SUCCESS,
        gross_total_cents=1,
        discount_cents=0,
        net_total_cents=1,
    )
    db_session.add(other_order)
    await db_session.flush()
    available_coupon.status = CouponStatus.REDEEMED
    available_coupon.redeemed_at = func.now()
    available_coupon.redeemed_by_order_id = other_order.id
    await db_session.flush()

    widget_id = widget.id
    cart_id = await _cart_with(client, widget, 2)
    resp = await client.post(
        f"/carts/{cart_id}/checkout",
        headers={"Idempotency-Key": "c-3"},
        json={"coupon_code": "SAVE-TEST"},
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "COUPON_ALREADY_REDEEMED"

    # the failed checkout rolled its transaction back: no order, stock untouched
    order_count = await db_session.scalar(
        select(func.count())
        .select_from(Order)
        .where(Order.cart_id == uuid.UUID(cart_id))
    )
    assert order_count == 0
    inventory = await db_session.scalar(
        select(Product.inventory).where(Product.id == widget_id)
    )
    assert inventory == 100  # untouched


async def test_insufficient_inventory_leaves_coupon_available(
    client, available_coupon, db_session
):
    product = Product(name="Scarce", unit_price_cents=500, inventory=10)
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)

    cart_id = await _cart_with(client, product, 3)
    # deplete below the cart quantity after adding
    product.inventory = 1
    await db_session.flush()

    resp = await client.post(
        f"/carts/{cart_id}/checkout",
        headers={"Idempotency-Key": "c-4"},
        json={"coupon_code": "SAVE-TEST"},
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INSUFFICIENT_INVENTORY"

    await db_session.refresh(available_coupon)
    assert available_coupon.status is CouponStatus.AVAILABLE
    assert available_coupon.redeemed_by_order_id is None


async def test_hundred_percent_coupon_nets_zero(client, widget, db_session):
    coupon = Coupon(
        milestone_number=EVERY * 2,
        discount_percent=100,
        code="FREE-100",
        status=CouponStatus.AVAILABLE,
    )
    db_session.add(coupon)
    await db_session.flush()

    cart_id = await _cart_with(client, widget, 4)  # gross 4000
    resp = await client.post(
        f"/carts/{cart_id}/checkout",
        headers={"Idempotency-Key": "c-5"},
        json={"coupon_code": "FREE-100"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["gross_total_cents"] == 4000
    assert body["discount_cents"] == 4000
    assert body["net_total_cents"] == 0


async def test_concurrent_checkout_same_coupon_only_one_wins(
    committing_client, committing_session: AsyncSession, tracked
):
    product = Product(
        name=f"Dual-{uuid.uuid4()}", unit_price_cents=1000, inventory=100
    )
    coupon = Coupon(
        milestone_number=EVERY,
        discount_percent=PERCENT,
        code=f"RACE-{uuid.uuid4().hex[:8]}",
        status=CouponStatus.AVAILABLE,
    )
    committing_session.add_all([product, coupon])
    await committing_session.commit()
    product_id, coupon_id, coupon_code = product.id, coupon.id, coupon.code
    tracked.product_ids.append(product_id)
    tracked.coupon_ids.append(coupon_id)

    cart_ids = []
    for _ in range(2):
        cid = (await committing_client.post("/carts")).json()["id"]
        cart_ids.append(cid)
        tracked.cart_ids.append(uuid.UUID(cid))
        await committing_client.post(
            f"/carts/{cid}/items",
            json={"product_id": str(product_id), "quantity": 2},
        )

    keys = [f"k-{uuid.uuid4()}" for _ in range(2)]
    tracked.idempotency_keys.extend(keys)
    r1, r2 = await asyncio.gather(
        committing_client.post(
            f"/carts/{cart_ids[0]}/checkout",
            headers={"Idempotency-Key": keys[0]},
            json={"coupon_code": coupon_code},
        ),
        committing_client.post(
            f"/carts/{cart_ids[1]}/checkout",
            headers={"Idempotency-Key": keys[1]},
            json={"coupon_code": coupon_code},
        ),
    )

    assert sorted([r1.status_code, r2.status_code]) == [201, 409]
    winner = r1 if r1.status_code == 201 else r2
    loser = r1 if r1.status_code == 409 else r2
    assert loser.json()["error"]["code"] == "COUPON_ALREADY_REDEEMED"
    assert winner.json()["discount_cents"] == 2000 * PERCENT // 100

    redeemed = await committing_session.get(Coupon, coupon_id)
    await committing_session.refresh(redeemed)
    assert redeemed.status is CouponStatus.REDEEMED
    assert str(redeemed.redeemed_by_order_id) == winner.json()["id"]

    orders_with_discount = await committing_session.scalar(
        select(func.count())
        .select_from(Order)
        .where(Order.discount_cents > 0, Order.cart_id.in_(
            [uuid.UUID(c) for c in cart_ids]
        ))
    )
    assert orders_with_discount == 1
