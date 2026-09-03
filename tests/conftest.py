"""Shared pytest fixtures.

Deliberately minimal — just enough to support the health smoke test. Future
fixtures (test database session, transactional rollback, seed data, etc.)
belong here.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# Placeholder for a future test DB session fixture:
#
# @pytest_asyncio.fixture
# async def db_session():
#     ...
