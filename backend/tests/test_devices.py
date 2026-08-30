import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module


pytestmark = pytest.mark.anyio


def client() -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url="http://test")


DEVICE = {
    "name": "rb-lab-01",
    "address": "192.0.2.10",
    "username": "backup",
    "password": "s3cret",
}


async def test_create_device_returns_201_without_password() -> None:
    async with client() as api:
        response = await api.post("/api/devices", json=DEVICE)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "rb-lab-01"
    assert body["model"] == "routeros"
    assert body["enabled"] is True
    assert "password" not in body


async def test_duplicate_name_returns_409() -> None:
    async with client() as api:
        first = await api.post("/api/devices", json=DEVICE)
        second = await api.post("/api/devices", json=DEVICE)

    assert first.status_code == 201
    assert second.status_code == 409


async def test_invalid_name_is_rejected() -> None:
    async with client() as api:
        response = await api.post(
            "/api/devices", json={**DEVICE, "name": "../etc/passwd"}
        )

    assert response.status_code == 422


async def test_list_and_get_devices() -> None:
    async with client() as api:
        created = (await api.post("/api/devices", json=DEVICE)).json()
        listed = await api.get("/api/devices")
        fetched = await api.get(f"/api/devices/{created['id']}")

    assert listed.status_code == 200
    assert [device["name"] for device in listed.json()] == ["rb-lab-01"]
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]


async def test_get_missing_device_returns_404() -> None:
    async with client() as api:
        response = await api.get("/api/devices/999")

    assert response.status_code == 404


async def test_patch_updates_fields() -> None:
    async with client() as api:
        created = (await api.post("/api/devices", json=DEVICE)).json()
        response = await api.patch(
            f"/api/devices/{created['id']}",
            json={"address": "192.0.2.20", "enabled": False},
        )

    assert response.status_code == 200
    assert response.json()["address"] == "192.0.2.20"
    assert response.json()["enabled"] is False


async def test_delete_device() -> None:
    async with client() as api:
        created = (await api.post("/api/devices", json=DEVICE)).json()
        deleted = await api.delete(f"/api/devices/{created['id']}")
        missing = await api.get(f"/api/devices/{created['id']}")

    assert deleted.status_code == 204
    assert missing.status_code == 404
