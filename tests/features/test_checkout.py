import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.carts.models import Cart, CartItem
from app.features.orders.models import Order, OrderItem
from app.features.products.models import Product

pytestmark = pytest.mark.asyncio

HEADERS = {"Idempotency-Key": "test-key-1"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def products(db_session: AsyncSession) -> dict[str, Product]:
    await db_session.execute(delete(OrderItem))
    await db_session.execute(delete(Order))
    await db_session.execute(delete(CartItem))
    await db_session.execute(delete(Cart))
    await db_session.execute(delete(Product))
    catalogue = {
        "widget": Product(name="Widget", unit_price_cents=1000, inventory=10),
        "gadget": Product(name="Gadget", unit_price_cents=2500, inventory=4),
    }
    db_session.add_all(catalogue.values())
    await db_session.flush()
    for product in catalogue.values():
        await db_session.refresh(product)
    return catalogue


async def _cart_with(client, product: Product, quantity: int) -> str:
    cart_id = (await client.post("/carts")).json()["id"]
    resp = await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(product.id), "quantity": quantity},
    )
    assert resp.status_code == 201
    return cart_id


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #


async def test_checkout_creates_order_and_decrements_inventory(
    client, products, db_session
):
    cart_id = await _cart_with(client, products["widget"], 3)

    resp = await client.post(f"/carts/{cart_id}/checkout", headers=HEADERS)

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "success"
    assert body["gross_total_cents"] == 3000
    assert body["discount_cents"] == 0
    assert body["net_total_cents"] == 3000
    assert len(body["items"]) == 1
    line = body["items"][0]
    assert line["product_name"] == "Widget"
    assert line["unit_price_cents"] == 1000
    assert line["quantity"] == 3
    assert line["line_total_cents"] == 3000

    inventory = await db_session.scalar(
        select(Product.inventory).where(Product.id == products["widget"].id)
    )
    assert inventory == 7

    cart = await db_session.get(Cart, uuid.UUID(cart_id))
    assert cart.status.value == "checked_out"


async def test_checkout_missing_idempotency_key_header_422(client, products):
    cart_id = await _cart_with(client, products["widget"], 1)

    resp = await client.post(f"/carts/{cart_id}/checkout")

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_get_order_returns_snapshots(client, products):
    cart_id = await _cart_with(client, products["gadget"], 2)
    order_id = (
        await client.post(f"/carts/{cart_id}/checkout", headers=HEADERS)
    ).json()["id"]

    resp = await client.get(f"/orders/{order_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == order_id
    assert body["items"][0]["product_name"] == "Gadget"
    assert body["net_total_cents"] == 5000


async def test_get_missing_order_returns_404(client):
    resp = await client.get(f"/orders/{uuid.uuid4()}")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# --------------------------------------------------------------------------- #
# idempotency (sequential retry)
# --------------------------------------------------------------------------- #


async def test_retry_same_key_returns_same_order_without_side_effects(
    client, products, db_session
):
    cart_id = await _cart_with(client, products["widget"], 2)

    first = await client.post(f"/carts/{cart_id}/checkout", headers=HEADERS)
    assert first.status_code == 201
    order_id = first.json()["id"]
    inventory_after_first = await db_session.scalar(
        select(Product.inventory).where(Product.id == products["widget"].id)
    )

    second = await client.post(f"/carts/{cart_id}/checkout", headers=HEADERS)

    assert second.status_code == 201
    assert second.json() == first.json()
    assert second.json()["id"] == order_id

    order_count = await db_session.scalar(select(func.count()).select_from(Order))
    assert order_count == 1
    inventory_after_second = await db_session.scalar(
        select(Product.inventory).where(Product.id == products["widget"].id)
    )
    assert inventory_after_second == inventory_after_first == 8


async def test_reusing_key_for_a_different_cart_is_rejected(client, products):
    cart_a = await _cart_with(client, products["widget"], 1)
    cart_b = await _cart_with(client, products["gadget"], 1)

    assert (
        await client.post(f"/carts/{cart_a}/checkout", headers=HEADERS)
    ).status_code == 201

    resp = await client.post(f"/carts/{cart_b}/checkout", headers=HEADERS)

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


# --------------------------------------------------------------------------- #
# validation / failure paths
# --------------------------------------------------------------------------- #


async def test_checkout_already_checked_out_cart_409(client, products):
    cart_id = await _cart_with(client, products["widget"], 1)
    assert (
        await client.post(
            f"/carts/{cart_id}/checkout", headers={"Idempotency-Key": "k-a"}
        )
    ).status_code == 201

    resp = await client.post(
        f"/carts/{cart_id}/checkout", headers={"Idempotency-Key": "k-b"}
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CART_ALREADY_CHECKED_OUT"


async def test_checkout_empty_cart_422(client, products):
    cart_id = (await client.post("/carts")).json()["id"]

    resp = await client.post(f"/carts/{cart_id}/checkout", headers=HEADERS)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "EMPTY_CART"


async def test_insufficient_inventory_aborts_with_no_partial_decrement(
    committing_client, committing_session: AsyncSession, tracked
):
    """Two-line cart; the second product is depleted after add but before
    checkout. Checkout must 409 and leave BOTH products' inventory untouched."""
    first = Product(name="AAA First", unit_price_cents=1000, inventory=10)
    second = Product(name="ZZZ Second", unit_price_cents=1000, inventory=10)
    committing_session.add_all([first, second])
    await committing_session.commit()
    first_id, second_id = first.id, second.id
    tracked.product_ids += [first_id, second_id]

    cart_id = (await committing_client.post("/carts")).json()["id"]
    tracked.cart_ids.append(uuid.UUID(cart_id))
    await committing_client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(first_id), "quantity": 2},
    )
    await committing_client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(second_id), "quantity": 3},
    )

    # deplete the second product behind the cart's back (simulates another
    # checkout / an admin edit happening between add-to-cart and checkout)
    await committing_session.execute(
        update(Product).where(Product.id == second_id).values(inventory=1)
    )
    await committing_session.commit()

    tracked.idempotency_keys.append("k-insuff")
    resp = await committing_client.post(
        f"/carts/{cart_id}/checkout", headers={"Idempotency-Key": "k-insuff"}
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INSUFFICIENT_INVENTORY"
    assert resp.json()["error"]["details"]["product_id"] == str(second_id)

    inv_first = await committing_session.scalar(
        select(Product.inventory).where(Product.id == first_id)
    )
    inv_second = await committing_session.scalar(
        select(Product.inventory).where(Product.id == second_id)
    )
    assert inv_first == 10  # not partially decremented
    assert inv_second == 1
    order_count = await committing_session.scalar(
        select(func.count())
        .select_from(Order)
        .where(Order.cart_id == uuid.UUID(cart_id))
    )
    assert order_count == 0


async def test_order_snapshot_survives_later_price_change(
    client, products, db_session
):
    cart_id = await _cart_with(client, products["widget"], 2)
    order_id = (
        await client.post(f"/carts/{cart_id}/checkout", headers=HEADERS)
    ).json()["id"]

    products["widget"].unit_price_cents = 999_999
    products["widget"].name = "Renamed Widget"
    await db_session.flush()

    body = (await client.get(f"/orders/{order_id}")).json()
    assert body["items"][0]["unit_price_cents"] == 1000
    assert body["items"][0]["product_name"] == "Widget"
    assert body["net_total_cents"] == 2000


# --------------------------------------------------------------------------- #
# concurrency
# --------------------------------------------------------------------------- #


async def test_concurrent_checkout_two_carts_one_unit_of_stock(
    committing_client, committing_session: AsyncSession, tracked
):
    product = Product(
        name=f"OneUnit-{uuid.uuid4()}", unit_price_cents=1500, inventory=1
    )
    committing_session.add(product)
    await committing_session.commit()
    product_id = product.id
    tracked.product_ids.append(product_id)

    cart_ids = []
    for _ in range(2):
        cid = (await committing_client.post("/carts")).json()["id"]
        cart_ids.append(cid)
        tracked.cart_ids.append(uuid.UUID(cid))
        await committing_client.post(
            f"/carts/{cid}/items",
            json={"product_id": str(product_id), "quantity": 1},
        )

    keys = ["conc-a", "conc-b"]
    tracked.idempotency_keys += keys
    r1, r2 = await asyncio.gather(
        committing_client.post(
            f"/carts/{cart_ids[0]}/checkout", headers={"Idempotency-Key": keys[0]}
        ),
        committing_client.post(
            f"/carts/{cart_ids[1]}/checkout", headers={"Idempotency-Key": keys[1]}
        ),
    )

    assert sorted([r1.status_code, r2.status_code]) == [201, 409]
    loser = r1 if r1.status_code == 409 else r2
    assert loser.json()["error"]["code"] == "INSUFFICIENT_INVENTORY"

    inventory = await committing_session.scalar(
        select(Product.inventory).where(Product.id == product_id)
    )
    assert inventory == 0

    order_count = await committing_session.scalar(
        select(func.count())
        .select_from(Order)
        .where(Order.cart_id.in_([uuid.UUID(c) for c in cart_ids]))
    )
    assert order_count == 1


async def test_concurrent_same_cart_same_key_creates_one_order(
    committing_client, committing_session: AsyncSession, tracked
):
    product = Product(
        name=f"Race-{uuid.uuid4()}", unit_price_cents=800, inventory=100
    )
    committing_session.add(product)
    await committing_session.commit()
    product_id = product.id
    tracked.product_ids.append(product_id)

    cart_id = (await committing_client.post("/carts")).json()["id"]
    tracked.cart_ids.append(uuid.UUID(cart_id))
    await committing_client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(product_id), "quantity": 2},
    )

    key = f"double-click-{uuid.uuid4()}"
    tracked.idempotency_keys.append(key)
    responses = await asyncio.gather(
        *(
            committing_client.post(
                f"/carts/{cart_id}/checkout", headers={"Idempotency-Key": key}
            )
            for _ in range(8)
        )
    )

    assert all(r.status_code == 201 for r in responses)
    order_ids = {r.json()["id"] for r in responses}
    assert len(order_ids) == 1

    order_count = await committing_session.scalar(
        select(func.count())
        .select_from(Order)
        .where(Order.cart_id == uuid.UUID(cart_id))
    )
    assert order_count == 1
    inventory = await committing_session.scalar(
        select(Product.inventory).where(Product.id == product_id)
    )
    assert inventory == 98
