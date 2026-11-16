from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app, lifespan


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    app = create_app(
        Settings(
            app_name="Test Inspector",
            environment="test",
            log_level="WARNING",
            database_path=tmp_path / "test.db",
        )
    )
    transport = ASGITransport(app=app)

    async with lifespan(app):
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as test_client:
            yield test_client
