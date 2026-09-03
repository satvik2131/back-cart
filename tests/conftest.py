"""Shared pytest fixtures.

Tests run against a **dedicated** database (``<DATABASE_URL db>_test``), created
and schema-loaded once per session from the ORM metadata, so the suite never
touches the dev database. Set ``TEST_DATABASE_URL`` to override.

Two client fixtures, for two kinds of test:

* ``client`` — every request shares one session bound to a single transaction
  that is rolled back on teardown (see DECISIONS.md §3). Total isolation.
* ``committing_client`` — every request gets its own real session that actually
  commits, for genuine-concurrency tests (a single shared transaction can't
  exercise row-level locking). Such tests start from an empty DB (the ``tracked``
  fixture clears it) and clean up their rows.

A ``NullPool`` engine backs both so no pooled connection outlives the per-test
event loop pytest-asyncio creates.
"""

import asyncio
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.features.carts.models import Cart, CartItem
from app.features.coupons.models import Coupon
from app.features.orders.models import IdempotencyKey, Order, OrderItem
from app.features.products.models import Product
from app.main import app

_main_url = make_url(settings.DATABASE_URL)
_test_db_name = (_main_url.database or "back_cart") + "_test"
TEST_DATABASE_URL = settings.TEST_DATABASE_URL or _main_url.set(
    database=_test_db_name
).render_as_string(hide_password=False)


async def _provision_test_db() -> None:
    admin_engine = create_async_engine(
        _main_url.set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    async with admin_engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": _test_db_name},
        )
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{_test_db_name}"'))
    await admin_engine.dispose()

    schema_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with schema_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await schema_engine.dispose()


asyncio.run(_provision_test_db())

test_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

# FK-safe delete order: referencing rows before their targets.
_TEARDOWN_ORDER = (OrderItem, Coupon, Order, IdempotencyKey, CartItem, Cart, Product)


async def clear_domain(session: AsyncSession) -> None:
    """Delete every domain row, in FK-safe order."""
    for model in _TEARDOWN_ORDER:
        await session.execute(delete(model))


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    connection = await test_engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override_get_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest_asyncio.fixture
async def committing_session() -> AsyncGenerator[AsyncSession, None]:
    """A real session (commits persist) for test setup and cleanup."""
    async with AsyncSession(test_engine, expire_on_commit=False) as session:
        yield session


class Tracked:
    """Records ids created by a committing test so they can be removed."""

    def __init__(self) -> None:
        self.product_ids: list = []
        self.cart_ids: list = []
        self.order_ids: list = []
        self.coupon_ids: list = []
        self.idempotency_keys: list = []


@pytest_asyncio.fixture
async def tracked(
    committing_session: AsyncSession,
) -> AsyncGenerator[Tracked, None]:
    """Cleanup registry for committing/concurrency tests.

    Starts each such test from an empty database and, on teardown, removes the
    ids that were registered (FK-safe: coupons before orders, orders before
    carts, everything before products).
    """
    from sqlalchemy import or_

    await clear_domain(committing_session)
    await committing_session.commit()

    registry = Tracked()
    try:
        yield registry
    finally:
        s = committing_session
        order_filter = []
        if registry.cart_ids:
            order_filter.append(Order.cart_id.in_(registry.cart_ids))
        if registry.order_ids:
            order_filter.append(Order.id.in_(registry.order_ids))

        if registry.coupon_ids:
            await s.execute(
                delete(Coupon).where(Coupon.id.in_(registry.coupon_ids))
            )
        if order_filter:
            await s.execute(delete(Order).where(or_(*order_filter)))
        if registry.idempotency_keys:
            await s.execute(
                delete(IdempotencyKey).where(
                    IdempotencyKey.key.in_(registry.idempotency_keys)
                )
            )
        if registry.cart_ids:
            await s.execute(delete(Cart).where(Cart.id.in_(registry.cart_ids)))
        if registry.product_ids:
            await s.execute(
                delete(Product).where(Product.id.in_(registry.product_ids))
            )
        await s.commit()


@pytest_asyncio.fixture
async def committing_client() -> AsyncGenerator[AsyncClient, None]:
    """Client whose every request gets its own real, committing session."""

    async def _override_get_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSession(test_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_session, None)
