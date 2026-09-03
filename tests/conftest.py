"""Shared pytest fixtures.

Two client fixtures, for two kinds of test:

* ``client`` — every request shares one session bound to a single transaction
  that is rolled back on teardown (see DECISIONS.md §3). Total isolation, no
  cleanup needed. Use for everything sequential.
* ``committing_client`` — every request gets its own real session that actually
  commits. Needed for genuine-concurrency tests, since a single shared
  transaction cannot exercise row-level locking. Tests using it must delete the
  rows they create (``committing_session`` is provided for setup/teardown).

A dedicated ``NullPool`` engine backs both so no pooled connection outlives the
per-test event loop that pytest-asyncio creates. Requires the schema to exist in
the target database (``alembic upgrade head``).
"""

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_session
from app.main import app

test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)


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
