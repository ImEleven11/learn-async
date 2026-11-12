from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app, lifespan


async def test_health_check_returns_service_status() -> None:
    app = create_app(
        Settings(
            app_name="Test Inspector",
            environment="test",
            log_level="WARNING",
        )
    )
    transport = ASGITransport(app=app)

    async with lifespan(app):
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "Test Inspector",
        "environment": "test",
    }
