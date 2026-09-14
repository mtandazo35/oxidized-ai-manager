import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module


pytestmark = pytest.mark.anyio


def client(auth_headers: dict[str, str]) -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers
    )


DEVICE = {
    "name": "rb-lab-01",
    "address": "192.0.2.10",
    "username": "backup",
    "password": "s3cret",
}


async def test_create_device_returns_201_without_password(auth_headers) -> None:
    async with client(auth_headers) as api:
        response = await api.post("/api/devices", json=DEVICE)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "rb-lab-01"
    assert body["model"] == "routeros"
    assert body["port"] == 22
    assert body["enabled"] is True
    assert "password" not in body


async def test_group_name_create_and_update(auth_headers) -> None:
    async with client(auth_headers) as api:
        created = (await api.post(
            "/api/devices", json={**DEVICE, "group_name": "EmpresaA"}
        )).json()
        patched = await api.patch(
            f"/api/devices/{created['id']}", json={"group_name": "EmpresaB"}
        )

    assert created["group_name"] == "EmpresaA"
    assert patched.json()["group_name"] == "EmpresaB"


async def test_per_device_backup_interval(auth_headers) -> None:
    async with client(auth_headers) as api:
        created = (await api.post(
            "/api/devices", json={**DEVICE, "backup_interval_minutes": 15}
        )).json()
        patched = await api.patch(
            f"/api/devices/{created['id']}", json={"backup_interval_minutes": 0}
        )

    assert created["backup_interval_minutes"] == 15
    assert patched.json()["backup_interval_minutes"] == 0


async def test_backup_interval_out_of_range_rejected(auth_headers) -> None:
    async with client(auth_headers) as api:
        response = await api.post(
            "/api/devices",
            json={**DEVICE, "name": "rb-x", "backup_interval_minutes": 99999},
        )

    assert response.status_code == 422


async def test_create_device_with_custom_port(auth_headers) -> None:
    async with client(auth_headers) as api:
        response = await api.post(
            "/api/devices", json={**DEVICE, "name": "rb-lab-09", "port": 2222}
        )

    assert response.status_code == 201
    assert response.json()["port"] == 2222


async def test_invalid_port_is_rejected(auth_headers) -> None:
    async with client(auth_headers) as api:
        response = await api.post(
            "/api/devices", json={**DEVICE, "name": "rb-lab-08", "port": 70000}
        )

    assert response.status_code == 422


async def test_duplicate_name_returns_409(auth_headers) -> None:
    async with client(auth_headers) as api:
        first = await api.post("/api/devices", json=DEVICE)
        second = await api.post("/api/devices", json=DEVICE)

    assert first.status_code == 201
    assert second.status_code == 409


async def test_a_path_like_name_cannot_escape_the_repository(auth_headers) -> None:
    """Se normaliza en vez de rechazarse, pero nunca puede salir del repositorio.

    Lo que protege a `backups.git` no es rechazar la entrada, sino que el
    nombre guardado jamás contenga barras ni empiece por punto.
    """
    async with client(auth_headers) as api:
        response = await api.post(
            "/api/devices", json={**DEVICE, "name": "../etc/passwd"}
        )

    assert response.status_code == 201
    guardado = response.json()["name"]
    assert guardado == "etc-passwd"
    assert "/" not in guardado and not guardado.startswith(".")


async def test_list_and_get_devices(auth_headers) -> None:
    async with client(auth_headers) as api:
        created = (await api.post("/api/devices", json=DEVICE)).json()
        listed = await api.get("/api/devices")
        fetched = await api.get(f"/api/devices/{created['id']}")

    assert listed.status_code == 200
    assert [device["name"] for device in listed.json()] == ["rb-lab-01"]
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]


async def test_get_missing_device_returns_404(auth_headers) -> None:
    async with client(auth_headers) as api:
        response = await api.get("/api/devices/999")

    assert response.status_code == 404


async def test_patch_updates_fields(auth_headers) -> None:
    async with client(auth_headers) as api:
        created = (await api.post("/api/devices", json=DEVICE)).json()
        response = await api.patch(
            f"/api/devices/{created['id']}",
            json={"address": "192.0.2.20", "enabled": False},
        )

    assert response.status_code == 200
    assert response.json()["address"] == "192.0.2.20"
    assert response.json()["enabled"] is False


async def test_backup_now_missing_device_returns_404(auth_headers) -> None:
    async with client(auth_headers) as api:
        response = await api.post("/api/devices/999/backup")

    assert response.status_code == 404


async def test_backup_now_reports_unreachable_oxidized(auth_headers) -> None:
    async with client(auth_headers) as api:
        created = (await api.post("/api/devices", json=DEVICE)).json()
        response = await api.post(f"/api/devices/{created['id']}/backup")

    assert response.status_code == 502


async def test_delete_device(auth_headers) -> None:
    async with client(auth_headers) as api:
        created = (await api.post("/api/devices", json=DEVICE)).json()
        deleted = await api.delete(f"/api/devices/{created['id']}")
        missing = await api.get(f"/api/devices/{created['id']}")

    assert deleted.status_code == 204
    assert missing.status_code == 404


# --- Nombre del equipo: lo ajusta el servidor -----------------------------


async def test_name_with_spaces_is_normalized(auth_headers) -> None:
    """El operador escribe el nombre como lo tiene en la cabeza."""
    async with client(auth_headers) as api:
        response = await api.post(
            "/api/devices",
            headers=auth_headers,
            json={"name": "Router Sucursal Norte", "address": "198.51.100.7", "port": 2232},
        )

    assert response.status_code == 201
    assert response.json()["name"] == "Router-Sucursal-Norte"


async def test_accents_and_separators_are_cleaned(auth_headers) -> None:
    async with client(auth_headers) as api:
        response = await api.post(
            "/api/devices",
            headers=auth_headers,
            json={"name": "Empresa - Señal (Norte)", "address": "192.0.2.1"},
        )

    assert response.json()["name"] == "Empresa-Senal-Norte"


async def test_a_name_with_nothing_usable_is_rejected(auth_headers) -> None:
    async with client(auth_headers) as api:
        response = await api.post(
            "/api/devices", headers=auth_headers, json={"name": "###", "address": "1.2.3.4"}
        )

    assert response.status_code == 422


async def test_rename_is_normalized_too(auth_headers) -> None:
    async with client(auth_headers) as api:
        creado = await api.post(
            "/api/devices",
            headers=auth_headers,
            json={"name": "rb-uno", "address": "192.0.2.1"},
        )
        response = await api.patch(
            f"/api/devices/{creado.json()['id']}",
            headers=auth_headers,
            json={"name": "Router Dos"},
        )

    assert response.json()["name"] == "Router-Dos"


# --- Primer respaldo al dar de alta ---------------------------------------


async def test_a_new_device_is_backed_up_right_away(
    auth_headers, no_network_backups
) -> None:
    """No tiene sentido esperar al ciclo programado para el primer respaldo."""
    async with client(auth_headers) as api:
        await api.post(
            "/api/devices", json={"name": "rb-nuevo", "address": "192.0.2.50"}
        )

    assert no_network_backups == ["rb-nuevo"]


async def test_a_paused_device_is_not_backed_up(
    auth_headers, no_network_backups
) -> None:
    async with client(auth_headers) as api:
        await api.post(
            "/api/devices",
            json={"name": "rb-pausado", "address": "192.0.2.51", "enabled": False},
        )

    assert no_network_backups == []


async def test_a_duplicate_does_not_trigger_a_backup(
    auth_headers, no_network_backups
) -> None:
    async with client(auth_headers) as api:
        await api.post(
            "/api/devices", json={"name": "rb-uno", "address": "192.0.2.52"}
        )
        await api.post(
            "/api/devices", json={"name": "rb-uno", "address": "192.0.2.53"}
        )

    assert no_network_backups == ["rb-uno"]


async def test_bulk_import_backs_up_everything_it_created(
    auth_headers, no_network_backups
) -> None:
    async with client(auth_headers) as api:
        await api.post(
            "/api/devices/import",
            json={"text": "rb-a,192.0.2.60\nrb-b,192.0.2.61\nlinea mala"},
        )

    assert no_network_backups == ["rb-a", "rb-b"]
