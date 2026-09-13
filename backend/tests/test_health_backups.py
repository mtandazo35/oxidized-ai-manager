"""`/health/backups` lo consulta Uptime Kuma sin token: debe alertar por
antigüedad y no revelar el inventario."""

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module


pytestmark = pytest.mark.anyio


def client() -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url="http://test")


async def add_device(api, auth_headers, name: str, **extra):
    return await api.post(
        "/api/devices",
        headers=auth_headers,
        json={"name": name, "address": "192.0.2.1", **extra},
    )


def age(repository, name: str, minutes: int) -> None:
    """Envejece la fecha de alta de un equipo en el repositorio de prueba."""
    for device in repository._devices.values():
        if device["name"] == name:
            device["created_at"] = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
                minutes=minutes
            )


async def test_no_devices_is_healthy() -> None:
    async with client() as api:
        response = await api.get("/health/backups")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "devices": 0,
        "stale": 0,
        "never_backed_up": 0,
        "interval_minutes": 60,
        "staleness_factor": 3.0,
    }


async def test_needs_no_authentication() -> None:
    async with client() as api:
        response = await api.get("/health/backups")

    assert response.status_code == 200


async def test_recently_added_device_is_not_yet_stale(
    auth_headers, device_repository
) -> None:
    async with client() as api:
        await add_device(api, auth_headers, "rb-nuevo")
        response = await api.get("/health/backups")

    assert response.status_code == 200
    assert response.json()["never_backed_up"] == 1
    assert response.json()["stale"] == 0


async def test_device_never_backed_up_past_the_deadline_is_stale(
    auth_headers, device_repository
) -> None:
    async with client() as api:
        await add_device(api, auth_headers, "rb-olvidado")
        age(device_repository, "rb-olvidado", minutes=60 * 5)  # 5 h > 60 min × 3
        response = await api.get("/health/backups")

    assert response.status_code == 503
    assert response.json()["status"] == "stale"
    assert response.json()["stale"] == 1


async def test_recent_success_clears_the_alert(
    auth_headers, device_repository, backup_event_repository
) -> None:
    async with client() as api:
        await add_device(api, auth_headers, "rb-al-dia")
        age(device_repository, "rb-al-dia", minutes=60 * 5)
        await backup_event_repository.record_event("rb-al-dia", "node_success", "abc")
        response = await api.get("/health/backups")

    assert response.status_code == 200
    assert response.json()["stale"] == 0


async def test_old_success_triggers_the_alert(
    auth_headers, device_repository, backup_event_repository
) -> None:
    async with client() as api:
        await add_device(api, auth_headers, "rb-atrasado")
        await backup_event_repository.record_event("rb-atrasado", "node_success", "a")
        backup_event_repository._events[0]["created_at"] = dt.datetime.now(
            dt.timezone.utc
        ) - dt.timedelta(hours=6)
        response = await api.get("/health/backups")

    assert response.status_code == 503
    assert response.json()["stale"] == 1
    assert response.json()["never_backed_up"] == 0


async def test_disabled_devices_are_ignored(auth_headers, device_repository) -> None:
    async with client() as api:
        await add_device(api, auth_headers, "rb-apagado", enabled=False)
        age(device_repository, "rb-apagado", minutes=60 * 24)
        response = await api.get("/health/backups")

    assert response.status_code == 200
    assert response.json()["devices"] == 0


async def test_per_device_interval_is_respected(
    auth_headers, device_repository
) -> None:
    async with client() as api:
        # Intervalo propio de 1 día: 2 h de atraso no debe alertar.
        await add_device(
            api, auth_headers, "rb-semanal", backup_interval_minutes=1440
        )
        age(device_repository, "rb-semanal", minutes=120)
        response = await api.get("/health/backups")

    assert response.status_code == 200


async def test_response_never_names_a_device(
    auth_headers, device_repository
) -> None:
    async with client() as api:
        await add_device(api, auth_headers, "rb-secreto")
        age(device_repository, "rb-secreto", minutes=60 * 5)
        response = await api.get("/health/backups")

    assert "rb-secreto" not in response.text
