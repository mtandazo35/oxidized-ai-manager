import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module


pytestmark = pytest.mark.anyio

TOKEN_HEADER = {"X-Oxidized-Token": "test-oxidized-token"}


def client() -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_missing_token_returns_401() -> None:
    async with client() as api:
        response = await api.get("/api/oxidized/nodes")

    assert response.status_code == 401


async def test_wrong_token_returns_401() -> None:
    async with client() as api:
        response = await api.get(
            "/api/oxidized/nodes", headers={"X-Oxidized-Token": "wrong"}
        )

    assert response.status_code == 401


async def test_empty_inventory_returns_placeholder() -> None:
    async with client() as api:
        response = await api.get("/api/oxidized/nodes", headers=TOKEN_HEADER)

    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "phase1-placeholder",
            "ip": "127.0.0.1",
            "model": "routeros",
            "username": "",
            "password": "",
        }
    ]


async def test_returns_enabled_devices_with_credentials() -> None:
    async with client() as api:
        await api.post(
            "/api/devices",
            json={
                "name": "rb-lab-01",
                "address": "192.0.2.10",
                "username": "backup",
                "password": "s3cret",
            },
        )
        await api.post(
            "/api/devices",
            json={
                "name": "rb-lab-02",
                "address": "192.0.2.11",
                "enabled": False,
            },
        )
        response = await api.get("/api/oxidized/nodes", headers=TOKEN_HEADER)

    assert response.status_code == 200
    nodes = response.json()
    assert [node["name"] for node in nodes] == ["rb-lab-01"]
    assert nodes[0]["ip"] == "192.0.2.10"
    assert nodes[0]["username"] == "backup"
    assert nodes[0]["password"] == "s3cret"
