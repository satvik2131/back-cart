import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.products.models import Product
from tests.conftest import clear_domain

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def seeded_products(db_session: AsyncSession) -> list[Product]:
    """A deterministic catalogue for the duration of one test.

    Clears the domain tables first (inside the test's rolled-back transaction,
    so committed data is untouched afterwards) and inserts a known set, letting
    tests assert on exact contents and counts.
    """
    await clear_domain(db_session)
    products = [
        Product(name="Alpha Mug", unit_price_cents=1200, inventory=100),
        Product(name="Bravo Bottle", unit_price_cents=2500, inventory=5),
        Product(name="Charlie Notebook", unit_price_cents=3499, inventory=2),
        Product(name="Delta Tote", unit_price_cents=900, inventory=40),
        Product(name="Echo Pin", unit_price_cents=750, inventory=0),
    ]
    db_session.add_all(products)
    await db_session.flush()
    for product in products:
        await db_session.refresh(product)
    return products


async def test_list_products_returns_seeded_data(client, seeded_products):
    resp = await client.get("/products")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == len(seeded_products)
    assert {p["name"] for p in body} == {p.name for p in seeded_products}

    by_name = {p["name"]: p for p in body}
    assert by_name["Charlie Notebook"]["unit_price_cents"] == 3499
    assert by_name["Charlie Notebook"]["inventory"] == 2
    assert by_name["Echo Pin"]["inventory"] == 0


async def test_get_product_by_valid_id(client, seeded_products):
    target = seeded_products[1]

    resp = await client.get(f"/products/{target.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(target.id)
    assert body["name"] == target.name
    assert body["unit_price_cents"] == target.unit_price_cents
    assert body["inventory"] == target.inventory


async def test_get_missing_product_returns_404_envelope(client, seeded_products):
    missing_id = uuid.uuid4()

    resp = await client.get(f"/products/{missing_id}")

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["details"]["product_id"] == str(missing_id)


async def test_money_and_inventory_serialize_as_integers(client, seeded_products):
    list_resp = await client.get("/products")
    assert list_resp.status_code == 200
    for item in list_resp.json():
        # bool is a subclass of int — exclude it explicitly.
        assert type(item["unit_price_cents"]) is int
        assert type(item["inventory"]) is int

    one_resp = await client.get(f"/products/{seeded_products[0].id}")
    assert one_resp.status_code == 200
    one = one_resp.json()
    assert type(one["unit_price_cents"]) is int
    assert type(one["inventory"]) is int
