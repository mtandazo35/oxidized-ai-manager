import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module


pytestmark = pytest.mark.anyio


async def request(path: str):
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def test_root_identifies_service() -> None:
    response = await request("/")

    assert response.status_code == 200
    assert response.json()["service"] == "Oxidized AI Manager"
    assert response.json()["docs"] == "/docs"


async def test_liveness_does_not_require_dependencies() -> None:
    response = await request("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_reports_healthy_dependencies(monkeypatch) -> None:
    async def healthy_checks(_settings):
        return {"postgres": True, "redis": True, "oxidized": True}

    monkeypatch.setattr(main_module, "check_dependencies", healthy_checks)
    response = await request("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"postgres": True, "redis": True, "oxidized": True},
    }


async def test_readiness_fails_when_a_dependency_is_unavailable(monkeypatch) -> None:
    async def degraded_checks(_settings):
        return {"postgres": True, "redis": False, "oxidized": True}

    monkeypatch.setattr(main_module, "check_dependencies", degraded_checks)
    response = await request("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["redis"] is False
