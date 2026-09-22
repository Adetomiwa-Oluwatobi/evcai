import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

import database
from database import Base
from main import app

TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/telemetry_test_db"


@pytest_asyncio.fixture
async def client():
    """
    Provides a test HTTP client wired to a freshly-reset test database.
    A NEW engine is created per test function (not at module import time)
    to avoid asyncpg conflicts across pytest-asyncio's per-test event loops.
    """
    test_engine = create_async_engine(TEST_DATABASE_URL)
    TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_session():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[database.get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.fixture
def admin_key():
    from config import settings
    return settings.admin_api_key
