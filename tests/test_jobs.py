from pathlib import Path
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app, lifespan


async def test_create_and_query_job(client: AsyncClient) -> None:
    create_response = await client.post(
        "/api/v1/jobs",
        json={
            "name": "  Local services  ",
            "urls": [
                "https://example.com/health",
                "https://example.org/status",
            ],
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["name"] == "Local services"
    assert created["status"] == "pending"
    assert created["total_count"] == 2
    assert created["completed_count"] == 0
    assert created["failed_count"] == 0

    get_response = await client.get(f"/api/v1/jobs/{created['id']}")

    assert get_response.status_code == 200
    assert get_response.json() == created


async def test_new_job_has_no_results(client: AsyncClient) -> None:
    create_response = await client.post(
        "/api/v1/jobs",
        json={"name": "No worker yet", "urls": ["https://example.com"]},
    )
    job = create_response.json()

    results_response = await client.get(f"/api/v1/jobs/{job['id']}/results")

    assert results_response.status_code == 200
    assert results_response.json() == {"job": job, "results": []}


async def test_unknown_job_returns_404(client: AsyncClient) -> None:
    job_id = uuid4()

    response = await client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 404
    assert response.json() == {"detail": f"Job {job_id} was not found"}


async def test_duplicate_urls_are_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/jobs",
        json={
            "name": "Duplicates",
            "urls": ["https://example.com", "https://example.com/"],
        },
    )

    assert response.status_code == 422


async def test_job_survives_application_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "persistent.db"
    settings = Settings(
        app_name="Test Inspector",
        environment="test",
        log_level="WARNING",
        database_path=database_path,
    )

    first_app = create_app(settings)
    async with lifespan(first_app):
        async with AsyncClient(
            transport=ASGITransport(app=first_app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/jobs",
                json={"name": "Persistent", "urls": ["https://example.com"]},
            )
            job_id = response.json()["id"]

    second_app = create_app(settings)
    async with lifespan(second_app):
        async with AsyncClient(
            transport=ASGITransport(app=second_app),
            base_url="http://test",
        ) as client:
            response = await client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["name"] == "Persistent"
