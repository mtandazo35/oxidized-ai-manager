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


async def test_bulk_add_credenciales(auth_headers) -> None:
    async with client(auth_headers) as api:
        d1 = await _crear(api, "rb-c1")
        d2 = await _crear(api, "rb-c2")
        r = await api.post("/api/devices/bulk-update", json={
            "scope": "devices", "device_ids": [d1["id"], d2["id"]],
            "extra_mode": "add",
            "extra_credentials": [
                {"username": "alt", "password": "x"},
                {"username": "alt2", "password": "y"},
            ],
        })
        assert r.status_code == 200
        assert r.json()["actualizados"] == 2
        devs = {d["name"]: d for d in (await api.get("/api/devices")).json()}
    assert devs["rb-c1"]["extra_usernames"] == ["alt", "alt2"]
    assert devs["rb-c2"]["extra_usernames"] == ["alt", "alt2"]


async def test_bulk_add_no_duplica_por_usuario(auth_headers) -> None:
    async with client(auth_headers) as api:
        d = (await api.post("/api/devices", json={
            "name": "rb-dup", "address": "192.0.2.10",
            "username": "u", "password": "p",
            "extra_credentials": [{"username": "alt", "password": "p1"}],
        })).json()
        await api.post("/api/devices/bulk-update", json={
            "scope": "devices", "device_ids": [d["id"]],
            "extra_mode": "add",
            "extra_credentials": [
                {"username": "alt", "password": "otra"},   # ya existe
                {"username": "u", "password": "z"},         # es la primaria
                {"username": "alt3", "password": "p3"},
            ],
        })
        devs = {x["name"]: x for x in (await api.get("/api/devices")).json()}
    assert devs["rb-dup"]["extra_usernames"] == ["alt", "alt3"]


async def test_bulk_remove_credenciales(auth_headers) -> None:
    async with client(auth_headers) as api:
        d = (await api.post("/api/devices", json={
            "name": "rb-rm", "address": "192.0.2.10",
            "username": "u", "password": "p",
            "extra_credentials": [
                {"username": "a", "password": "1"},
                {"username": "b", "password": "2"},
                {"username": "c", "password": "3"},
            ],
        })).json()
        r = await api.post("/api/devices/bulk-update", json={
            "scope": "devices", "device_ids": [d["id"]],
            "extra_mode": "remove",
            "extra_credentials": [{"username": "b"}, {"username": "c"}],
        })
        assert r.json()["actualizados"] == 1
        devs = {x["name"]: x for x in (await api.get("/api/devices")).json()}
    assert devs["rb-rm"]["extra_usernames"] == ["a"]


async def test_bulk_add_sin_credenciales_422(auth_headers) -> None:
    async with client(auth_headers) as api:
        d = await _crear(api, "rb-vacio")
        r = await api.post("/api/devices/bulk-update", json={
            "scope": "devices", "device_ids": [d["id"]],
            "extra_mode": "add", "extra_credentials": [],
        })
    assert r.status_code == 422
