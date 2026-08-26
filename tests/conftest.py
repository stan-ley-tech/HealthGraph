import os

# Set before any `app.*` import triggers `get_settings()` (which is
# `lru_cache`d — once any module imports it, env changes stop mattering).
# `setdefault` so a CI-provided value always wins over this.
os.environ.setdefault("RATE_LIMIT_REQUESTS", "100000")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use")

import uuid  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings  # noqa: E402
from app.database.session import get_db_session  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """One connection, one outer transaction per test. Every session
    handed out during the test — including the one
    `AuditService.record()` opens independently, patched below — joins
    that transaction as a SAVEPOINT, so an internal `session.commit()`
    only releases the savepoint rather than the real transaction. The
    final rollback discards everything the test did, so tests never
    need cleanup and never collide with each other's data.

    Uses its own engine rather than `app.database.session.engine`: that
    engine is a module-level singleton whose asyncpg connections bind to
    whatever event loop is running the first time it's used, and
    pytest-asyncio hands each test function a fresh loop by default. A
    per-test engine, torn down at the end of the test, keeps every
    asyncpg connection on the one loop that's actually running it.
    """
    test_engine = create_async_engine(get_settings().database_url)
    try:
        async with test_engine.connect() as connection:
            outer_transaction = await connection.begin()
            session_factory = async_sessionmaker(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            session = session_factory()

            import app.services.audit_service as audit_service_module

            original_session_local = audit_service_module.AsyncSessionLocal
            audit_service_module.AsyncSessionLocal = session_factory
            try:
                yield session
            finally:
                audit_service_module.AsyncSessionLocal = original_session_local
                await session.close()
                await outer_transaction.rollback()
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client wired to the real FastAPI app, but with the DB
    dependency overridden to the test's transactional session — so REST
    and GraphQL requests made through this client are rolled back at
    the end of the test exactly like direct repository calls are.
    """

    async def _override_get_db_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _override_get_db_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()


@pytest.fixture
def unique_email() -> str:
    return f"test-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture
def unique_mrn() -> str:
    return f"MRN-TEST-{uuid.uuid4().hex[:8].upper()}"
