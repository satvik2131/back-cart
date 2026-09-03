import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.carts.models import Cart, CartItem, CartStatus
from app.features.products.models import Product

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def products(db_session: AsyncSession) -> dict[str, Product]:
    """A small known catalogue for the duration of one test."""
    await db_session.execute(delete(CartItem))
    await db_session.execute(delete(Cart))
    await db_session.execute(delete(Product))
    catalogue = {
        "mug": Product(name="Mug", unit_price_cents=1200, inventory=100),
        "bottle": Product(name="Bottle", unit_price_cents=2500, inventory=3),
        "pin": Product(name="Pin", unit_price_cents=750, inventory=0),
    }
    db_session.add_all(catalogue.values())
    await db_session.flush()
    for product in catalogue.values():
        await db_session.refresh(product)
    return catalogue


async def _create_cart(client) -> str:
    resp = await client.post("/carts")
    assert resp.status_code == 201
    return resp.json()["id"]


# --------------------------------------------------------------------------- #
# create / view / totals
# --------------------------------------------------------------------------- #


async def test_create_cart_is_open_and_empty(client):
    resp = await client.post("/carts")

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "open"
    assert body["items"] == []
    assert body["total_cents"] == 0


async def test_add_items_and_view_cart_totals(client, products):
    cart_id = await _create_cart(client)

    await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(products["mug"].id), "quantity": 2},
    )
    resp = await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(products["bottle"].id), "quantity": 1},
    )
    assert resp.status_code == 201

    view = await client.get(f"/carts/{cart_id}")
    assert view.status_code == 200
    body = view.json()

    lines = {line["product_name"]: line for line in body["items"]}
    assert lines["Mug"]["quantity"] == 2
    assert lines["Mug"]["unit_price_cents"] == 1200
    assert lines["Mug"]["line_subtotal_cents"] == 2400
    assert lines["Bottle"]["line_subtotal_cents"] == 2500
    assert body["total_cents"] == 2400 + 2500


async def test_cart_view_reflects_current_product_price(
    client, products, db_session: AsyncSession
):
    """Totals are computed live — a later price change is reflected on read,
    because the cart stores no price of its own."""
    cart_id = await _create_cart(client)
    await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(products["mug"].id), "quantity": 3},
    )

    products["mug"].unit_price_cents = 1500
    await db_session.flush()

    view = await client.get(f"/carts/{cart_id}")
    line = view.json()["items"][0]
    assert line["unit_price_cents"] == 1500
    assert line["line_subtotal_cents"] == 4500


async def test_get_missing_cart_returns_404(client):
    resp = await client.get(f"/carts/{uuid.uuid4()}")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# --------------------------------------------------------------------------- #
# add-item validation
# --------------------------------------------------------------------------- #


async def test_adding_same_product_twice_increments_quantity(client, products):
    cart_id = await _create_cart(client)
    payload = {"product_id": str(products["mug"].id), "quantity": 2}

    await client.post(f"/carts/{cart_id}/items", json=payload)
    resp = await client.post(f"/carts/{cart_id}/items", json=payload)

    assert resp.status_code == 201
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["quantity"] == 4


async def test_add_nonexistent_product_returns_404(client, products):
    cart_id = await _create_cart(client)

    resp = await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(uuid.uuid4()), "quantity": 1},
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_add_malformed_product_id_returns_422(client, products):
    cart_id = await _create_cart(client)

    resp = await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": "not-a-uuid", "quantity": 1},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("bad_quantity", [0, -1, -50])
async def test_add_non_positive_quantity_returns_422(client, products, bad_quantity):
    cart_id = await _create_cart(client)

    resp = await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(products["mug"].id), "quantity": bad_quantity},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_add_more_than_available_inventory_returns_409(client, products):
    cart_id = await _create_cart(client)

    resp = await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(products["bottle"].id), "quantity": 5},
    )

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "INSUFFICIENT_INVENTORY"
    assert body["error"]["details"]["available"] == 3
    assert body["error"]["details"]["requested"] == 5


async def test_incremental_add_crossing_inventory_limit_returns_409(client, products):
    """Soft check is against the resulting total, not just this request."""
    cart_id = await _create_cart(client)
    ok = await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(products["bottle"].id), "quantity": 2},
    )
    assert ok.status_code == 201

    resp = await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(products["bottle"].id), "quantity": 2},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["details"]["requested"] == 4

    # the rejected add did not persist
    view = await client.get(f"/carts/{cart_id}")
    assert view.json()["items"][0]["quantity"] == 2


# --------------------------------------------------------------------------- #
# update / remove
# --------------------------------------------------------------------------- #


async def test_update_item_quantity(client, products):
    cart_id = await _create_cart(client)
    add = await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(products["mug"].id), "quantity": 1},
    )
    item_id = add.json()["items"][0]["id"]

    resp = await client.patch(
        f"/carts/{cart_id}/items/{item_id}", json={"quantity": 5}
    )

    assert resp.status_code == 200
    assert resp.json()["items"][0]["quantity"] == 5


async def test_update_item_quantity_to_zero_removes_it(client, products):
    cart_id = await _create_cart(client)
    add = await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(products["mug"].id), "quantity": 2},
    )
    item_id = add.json()["items"][0]["id"]

    resp = await client.patch(
        f"/carts/{cart_id}/items/{item_id}", json={"quantity": 0}
    )

    assert resp.status_code == 200
    assert resp.json()["items"] == []

    view = await client.get(f"/carts/{cart_id}")
    assert view.json()["items"] == []


async def test_update_item_over_inventory_returns_409(client, products):
    cart_id = await _create_cart(client)
    add = await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(products["bottle"].id), "quantity": 1},
    )
    item_id = add.json()["items"][0]["id"]

    resp = await client.patch(
        f"/carts/{cart_id}/items/{item_id}", json={"quantity": 10}
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INSUFFICIENT_INVENTORY"


async def test_delete_item(client, products):
    cart_id = await _create_cart(client)
    add = await client.post(
        f"/carts/{cart_id}/items",
        json={"product_id": str(products["mug"].id), "quantity": 1},
    )
    item_id = add.json()["items"][0]["id"]

    resp = await client.delete(f"/carts/{cart_id}/items/{item_id}")

    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_delete_missing_item_returns_404(client, products):
    cart_id = await _create_cart(client)

    resp = await client.delete(f"/carts/{cart_id}/items/{uuid.uuid4()}")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# --------------------------------------------------------------------------- #
# checked-out cart is immutable
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def checked_out_cart(db_session: AsyncSession, products) -> Cart:
    cart = Cart(status=CartStatus.CHECKED_OUT)
    db_session.add(cart)
    await db_session.flush()
    await db_session.refresh(cart)
    return cart


async def test_add_item_to_checked_out_cart_returns_409(
    client, products, checked_out_cart
):
    resp = await client.post(
        f"/carts/{checked_out_cart.id}/items",
        json={"product_id": str(products["mug"].id), "quantity": 1},
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CART_ALREADY_CHECKED_OUT"


async def test_patch_item_on_checked_out_cart_returns_409(
    client, products, checked_out_cart
):
    resp = await client.patch(
        f"/carts/{checked_out_cart.id}/items/{uuid.uuid4()}",
        json={"quantity": 2},
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CART_ALREADY_CHECKED_OUT"


async def test_delete_item_on_checked_out_cart_returns_409(
    client, products, checked_out_cart
):
    resp = await client.delete(
        f"/carts/{checked_out_cart.id}/items/{uuid.uuid4()}"
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CART_ALREADY_CHECKED_OUT"


# --------------------------------------------------------------------------- #
# concurrency — genuine parallel requests, real commits
# --------------------------------------------------------------------------- #


async def test_concurrent_add_same_product_no_lost_updates_or_duplicates(
    committing_client, committing_session: AsyncSession
):
    """Guards invariant 2's neighbourhood: N parallel adds of the same product
    to the same cart must yield exactly one row whose quantity is the sum."""
    product = Product(
        name=f"Concurrent-{uuid.uuid4()}", unit_price_cents=500, inventory=1000
    )
    committing_session.add(product)
    await committing_session.commit()

    cart_id = (await committing_client.post("/carts")).json()["id"]

    try:
        n = 15
        responses = await asyncio.gather(
            *(
                committing_client.post(
                    f"/carts/{cart_id}/items",
                    json={"product_id": str(product.id), "quantity": 1},
                )
                for _ in range(n)
            )
        )
        assert all(r.status_code == 201 for r in responses)

        rows = (
            await committing_session.execute(
                select(CartItem).where(CartItem.cart_id == uuid.UUID(cart_id))
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].quantity == n
    finally:
        await committing_session.execute(
            delete(Cart).where(Cart.id == uuid.UUID(cart_id))
        )
        await committing_session.execute(
            delete(Product).where(Product.id == product.id)
        )
        await committing_session.commit()
