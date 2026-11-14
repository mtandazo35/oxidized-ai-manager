"""Credencial SSH alternativa + validación de cuál conecta (apartado 1)."""
import pytest
from httpx import ASGITransport, AsyncClient

import app.devices as devices_module
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
    "username2": "u2",
    "password2": "p2",
}


async def test_segunda_credencial_se_guarda_sin_devolver_clave(auth_headers) -> None:
    async with client(auth_headers) as api:
        body = (await api.post("/api/devices", json=BASE)).json()
    assert body["username2"] == "u2"
    assert body["active_credential"] == 1
    assert "password2" not in body
    assert "password" not in body


async def test_validar_elige_la_que_conecta(
    auth_headers, device_repository, monkeypatch
) -> None:
    async def fake(address, port, username, password):
        return "ok" if username == "u2" else "credenciales"

    monkeypatch.setattr(devices_module, "_validar_acceso_ssh", fake)
    async with client(auth_headers) as api:
        dev = (await api.post("/api/devices", json=BASE)).json()
        resp = await api.post(f"/api/devices/{dev['id']}/validate-credentials")

    assert resp.status_code == 200
    body = resp.json()
    assert body["cred1"] == "credenciales"
    assert body["cred2"] == "ok"
    assert body["activa"] == 2
    # Oxidized debe recibir la credencial 2.
    nodes = await device_repository.list_oxidized_nodes()
    nodo = next(n for n in nodes if n["name"] == "rb-cred")
    assert nodo["username"] == "u2"
    assert nodo["password"] == "p2"


async def test_validar_prefiere_la_primaria_si_ambas_entran(
    auth_headers, device_repository, monkeypatch
) -> None:
    async def fake(address, port, username, password):
        return "ok"

    monkeypatch.setattr(devices_module, "_validar_acceso_ssh", fake)
    async with client(auth_headers) as api:
        dev = (await api.post("/api/devices", json=BASE)).json()
        body = (
            await api.post(f"/api/devices/{dev['id']}/validate-credentials")
        ).json()
    assert body["activa"] == 1
    nodes = await device_repository.list_oxidized_nodes()
    assert next(n for n in nodes if n["name"] == "rb-cred")["username"] == "u1"


async def test_validar_sin_conexion_no_cambia_activa(
    auth_headers, device_repository, monkeypatch
) -> None:
    async def fake(address, port, username, password):
        return "sin_conexion"

    monkeypatch.setattr(devices_module, "_validar_acceso_ssh", fake)
    async with client(auth_headers) as api:
        dev = (await api.post("/api/devices", json=BASE)).json()
        body = (
            await api.post(f"/api/devices/{dev['id']}/validate-credentials")
        ).json()
    assert body["activa"] is None
    # Se queda en la primaria.
    nodes = await device_repository.list_oxidized_nodes()
    assert next(n for n in nodes if n["name"] == "rb-cred")["username"] == "u1"


async def test_validar_equipo_inexistente_404(auth_headers) -> None:
    async with client(auth_headers) as api:
        resp = await api.post("/api/devices/9999/validate-credentials")
    assert resp.status_code == 404
