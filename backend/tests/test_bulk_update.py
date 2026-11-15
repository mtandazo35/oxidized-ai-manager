"""Cambios masivos de usuario/clave/puerto/omitir-respaldos."""
import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module


pytestmark = pytest.mark.anyio


def client(auth_headers: dict[str, str]) -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers
    )


async def _crear(api, name, group="", port=22):
    return (await api.post("/api/devices", json={
        "name": name, "address": "192.0.2.10", "port": port,
        "username": "u", "password": "p", "group_name": group,
    })).json()


async def test_bulk_update_requiere_login() -> None:
    async with client({}) as api:
        r = await api.post(
            "/api/devices/bulk-update",
            json={"scope": "all", "port": 2222},
        )
    assert r.status_code == 401


async def test_bulk_update_por_empresa(auth_headers) -> None:
    async with client(auth_headers) as api:
        a1 = await _crear(api, "rb-a1", "EmpA")
        a2 = await _crear(api, "rb-a2", "EmpA")
        b1 = await _crear(api, "rb-b1", "EmpB")
        r = await api.post("/api/devices/bulk-update", json={
            "scope": "group", "group": "EmpA", "port": 2222,
        })
        assert r.status_code == 200
        assert r.json()["actualizados"] == 2
        devs = {d["name"]: d for d in (await api.get("/api/devices")).json()}
    assert devs["rb-a1"]["port"] == 2222
    assert devs["rb-a2"]["port"] == 2222
    assert devs["rb-b1"]["port"] == 22  # otra empresa intacta


async def test_bulk_update_seleccionados_cambia_usuario(auth_headers) -> None:
    async with client(auth_headers) as api:
        d1 = await _crear(api, "rb-s1")
        d2 = await _crear(api, "rb-s2")
        await _crear(api, "rb-s3")
        r = await api.post("/api/devices/bulk-update", json={
            "scope": "devices", "device_ids": [d1["id"], d2["id"]],
            "username": "nuevo",
        })
        assert r.json()["actualizados"] == 2
        devs = {d["name"]: d for d in (await api.get("/api/devices")).json()}
    assert devs["rb-s1"]["username"] == "nuevo"
    assert devs["rb-s2"]["username"] == "nuevo"
    assert devs["rb-s3"]["username"] == "u"


async def test_bulk_update_omitir_respaldos(auth_headers, device_repository) -> None:
    async with client(auth_headers) as api:
        d1 = await _crear(api, "rb-o1")
        await api.post("/api/devices/bulk-update", json={
            "scope": "devices", "device_ids": [d1["id"]], "enabled": False,
        })
    # Omitido = sigue existiendo pero Oxidized ya no lo recibe.
    nodes = await device_repository.list_oxidized_nodes()
    assert all(n["name"] != "rb-o1" for n in nodes)
    devs = {d["name"]: d for d in await device_repository.list_devices()}
    assert devs["rb-o1"]["enabled"] is False


async def test_bulk_update_sin_cambios_422(auth_headers) -> None:
    async with client(auth_headers) as api:
        r = await api.post("/api/devices/bulk-update", json={"scope": "all"})
    assert r.status_code == 422


async def test_bulk_update_group_sin_empresa_422(auth_headers) -> None:
    async with client(auth_headers) as api:
        r = await api.post(
            "/api/devices/bulk-update", json={"scope": "group", "port": 2222},
        )
    assert r.status_code == 422
