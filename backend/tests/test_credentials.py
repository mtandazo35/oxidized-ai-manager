"""Credenciales SSH adicionales + validación de cuál conecta (lista dinámica)."""
import pytest
from httpx import ASGITransport, AsyncClient

import app.backups as backups_module
from app import main as main_module


pytestmark = pytest.mark.anyio


def client(auth_headers: dict[str, str]) -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers
    )


BASE = {
    "name": "rb-cred",
    "address": "192.0.2.50",
    "username": "u1",
    "password": "p1",
    "extra_credentials": [{"username": "u2", "password": "p2"}],
}


def _ssh_ok_para(*usuarios):
    """Fake de _validar_acceso_ssh: 'ok' para esos usuarios, 'credenciales' resto."""
    async def fake(address, port, username, password):
        return "ok" if username in usuarios else "credenciales"

    return fake


async def test_adicionales_se_guardan_sin_devolver_clave(auth_headers) -> None:
    async with client(auth_headers) as api:
        body = (await api.post("/api/devices", json=BASE)).json()
    assert body["extra_usernames"] == ["u2"]
    assert body["active_credential"] == 1
    assert "password" not in body
    assert "extra_credentials" not in body


async def test_validar_elige_la_que_conecta(
    auth_headers, device_repository, monkeypatch
) -> None:
    monkeypatch.setattr(backups_module, "_validar_acceso_ssh", _ssh_ok_para("u2"))
    async with client(auth_headers) as api:
        dev = (await api.post("/api/devices", json=BASE)).json()
        resp = await api.post(f"/api/devices/{dev['id']}/validate-credentials")

    assert resp.status_code == 200
    body = resp.json()
    estados = {r["idx"]: r["estado"] for r in body["resultados"]}
    assert estados == {1: "credenciales", 2: "ok"}
    assert body["activa"] == 2
    assert body["cambio"] is True
    nodes = await device_repository.list_oxidized_nodes()
    nodo = next(n for n in nodes if n["name"] == "rb-cred")
    assert nodo["username"] == "u2"
    assert nodo["password"] == "p2"


async def test_failover_a_la_tercera_credencial(
    auth_headers, device_repository, monkeypatch
) -> None:
    datos = {
        "name": "rb-tres",
        "address": "192.0.2.51",
        "username": "u1",
        "password": "p1",
        "extra_credentials": [
            {"username": "u2", "password": "p2"},
            {"username": "u3", "password": "p3"},
        ],
    }
    monkeypatch.setattr(backups_module, "_validar_acceso_ssh", _ssh_ok_para("u3"))
    async with client(auth_headers) as api:
        dev = (await api.post("/api/devices", json=datos)).json()
        body = (
            await api.post(f"/api/devices/{dev['id']}/validate-credentials")
        ).json()
    assert body["activa"] == 3  # primaria=1, u2=2, u3=3
    nodes = await device_repository.list_oxidized_nodes()
    assert next(n for n in nodes if n["name"] == "rb-tres")["username"] == "u3"


async def test_validar_prefiere_la_primera_que_conecta(
    auth_headers, device_repository, monkeypatch
) -> None:
    monkeypatch.setattr(
        backups_module, "_validar_acceso_ssh", _ssh_ok_para("u1", "u2")
    )
    async with client(auth_headers) as api:
        dev = (await api.post("/api/devices", json=BASE)).json()
        body = (
            await api.post(f"/api/devices/{dev['id']}/validate-credentials")
        ).json()
    assert body["activa"] == 1
    nodes = await device_repository.list_oxidized_nodes()
    assert next(n for n in nodes if n["name"] == "rb-cred")["username"] == "u1"


async def test_validar_ninguna_conecta_no_cambia_activa(
    auth_headers, device_repository, monkeypatch
) -> None:
    async def fake(address, port, username, password):
        return "sin_conexion"

    monkeypatch.setattr(backups_module, "_validar_acceso_ssh", fake)
    async with client(auth_headers) as api:
        dev = (await api.post("/api/devices", json=BASE)).json()
        body = (
            await api.post(f"/api/devices/{dev['id']}/validate-credentials")
        ).json()
    assert body["activa"] is None
    assert body["cambio"] is False
    nodes = await device_repository.list_oxidized_nodes()
    assert next(n for n in nodes if n["name"] == "rb-cred")["username"] == "u1"


async def test_validar_equipo_inexistente_404(auth_headers) -> None:
    async with client(auth_headers) as api:
        resp = await api.post("/api/devices/9999/validate-credentials")
    assert resp.status_code == 404
