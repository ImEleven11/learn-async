from httpx import AsyncClient


async def test_health_check_returns_service_status(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "Test Inspector",
        "environment": "test",
    }
