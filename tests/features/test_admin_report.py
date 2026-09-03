import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.carts.models import Cart, CartStatus
from app.features.coupons.models import Coupon, CouponStatus
from app.features.orders.models import Order, OrderItem, OrderStatus
from app.features.products.models import Product
from tests.conftest import clear_domain

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _clean(db_session: AsyncSession) -> None:
    await clear_domain(db_session)


async def _order(
    session: AsyncSession,
    product: Product,
    quantity: int,
    gross: int,
    discount: int,
) -> Order:
    cart = Cart(status=CartStatus.CHECKED_OUT)
    session.add(cart)
    await session.flush()
    order = Order(
        cart_id=cart.id,
        status=OrderStatus.SUCCESS,
        gross_total_cents=gross,
        discount_cents=discount,
        net_total_cents=gross - discount,
    )
    session.add(order)
    await session.flush()
    session.add(
        OrderItem(
            order_id=order.id,
            product_id=product.id,
            product_name_snapshot=product.name,
            unit_price_cents_snapshot=gross // quantity,
            quantity=quantity,
            line_total_cents=gross,
        )
    )
    await session.flush()
    return order


async def test_report_empty_state_is_all_zeros(client):
    resp = await client.get("/admin/report")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "total_successful_orders": 0,
        "gross_revenue_cents": 0,
        "total_discount_cents": 0,
        "net_revenue_cents": 0,
        "coupons_generated": 0,
        "coupons_available": 0,
        "coupons_redeemed": 0,
        "quantity_sold_by_product": [],
    }


async def test_report_reconciles_with_seeded_data(client, db_session):
    p1 = Product(name="Alpha", unit_price_cents=1000, inventory=100)
    p2 = Product(name="Beta", unit_price_cents=500, inventory=100)
    db_session.add_all([p1, p2])
    await db_session.flush()

    await _order(db_session, p1, quantity=2, gross=2000, discount=0)
    await _order(db_session, p1, quantity=1, gross=1000, discount=100)
    o3 = await _order(db_session, p2, quantity=4, gross=2000, discount=200)

    redeemed = Coupon(
        milestone_number=5,
        discount_percent=10,
        code="R-1",
        status=CouponStatus.REDEEMED,
        redeemed_at=func.now(),
        redeemed_by_order_id=o3.id,
    )
    available = Coupon(
        milestone_number=10, discount_percent=10, code="A-1",
        status=CouponStatus.AVAILABLE,
    )
    db_session.add_all([redeemed, available])
    await db_session.flush()

    body = (await client.get("/admin/report")).json()

    assert body["total_successful_orders"] == 3
    assert body["gross_revenue_cents"] == 5000
    assert body["total_discount_cents"] == 300
    assert body["net_revenue_cents"] == 4700

    # reconcile net against the per-order sum
    net_sum = await db_session.scalar(
        select(func.coalesce(func.sum(Order.net_total_cents), 0))
    )
    assert body["net_revenue_cents"] == net_sum

    assert body["coupons_generated"] == 2
    assert body["coupons_available"] == 1
    assert body["coupons_redeemed"] == 1

    sales = {r["product_name"]: r["quantity_sold"] for r in body["quantity_sold_by_product"]}
    assert sales == {"Alpha": 3, "Beta": 4}


async def test_report_is_read_only_and_repeatable(client, db_session):
    product = Product(name="Gamma", unit_price_cents=1000, inventory=100)
    db_session.add(product)
    await db_session.flush()
    await _order(db_session, product, quantity=1, gross=1000, discount=0)

    async def counts() -> tuple[int, int, int]:
        return (
            await db_session.scalar(select(func.count()).select_from(Order)),
            await db_session.scalar(select(func.count()).select_from(OrderItem)),
            await db_session.scalar(select(func.count()).select_from(Coupon)),
        )

    before = await counts()
    first = (await client.get("/admin/report")).json()
    second = (await client.get("/admin/report")).json()
    after = await counts()

    assert first == second
    assert before == after
