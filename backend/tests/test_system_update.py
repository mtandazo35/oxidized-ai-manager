"""Versión desplegada y solicitud de actualización.

Lo que importa aquí es que el backend **no ejecuta** la actualización: solo
deja una petición, y esa petición no puede llevar rama, commit ni URL.
"""

import json
import subprocess

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.config import get_settings
from app.updater import REQUEST_FILE, STATUS_FILE, request_update


pytestmark = pytest.mark.anyio


def client() -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def channel(tmp_path):
    directory = tmp_path / "canal"
    directory.mkdir()
    settings = get_settings()
    original = settings.update_channel_dir
    settings.update_channel_dir = str(directory)
    yield directory
    settings.update_channel_dir = original


@pytest.fixture
def repo(tmp_path):
    """Repositorio de verdad: el endpoint lee el commit con git."""
    path = tmp_path / "repo"
    path.mkdir()
    run = lambda *a: subprocess.run(
        ["git", "-C", str(path), *a], check=True, capture_output=True
    )
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    (path / "archivo.txt").write_text("contenido")
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "primer commit")
    settings = get_settings()
    original = settings.repo_git_dir
    settings.repo_git_dir = str(path / ".git")
    yield path
    settings.repo_git_dir = original


async def test_version_requires_admin(make_user, repo, channel) -> None:
    headers = make_user("op-a", role="operador", group_name="EmpresaA")

    async with client() as api:
        response = await api.get("/api/system/version", headers=headers)

    assert response.status_code == 403


async def test_version_reports_the_deployed_commit(
    auth_headers, repo, channel
) -> None:
    async with client() as api:
        response = await api.get("/api/system/version", headers=auth_headers)

    body = response.json()
    assert response.status_code == 200
    assert len(body["commit"]) == 40
    assert body["subject"] == "primer commit"
    # Sin remoto configurado no se puede comparar, pero la versión local sí sale.
    assert body["error"]
    assert body["update_available"] is False


async def test_version_survives_a_missing_repo(auth_headers, channel) -> None:
    settings = get_settings()
    original = settings.repo_git_dir
    settings.repo_git_dir = "/no/existe/.git"
    try:
        async with client() as api:
            response = await api.get("/api/system/version", headers=auth_headers)
    finally:
        settings.repo_git_dir = original

    assert response.status_code == 200
    assert "repositorio" in response.json()["error"]


async def test_update_only_leaves_a_request(auth_headers, channel) -> None:
    async with client() as api:
        response = await api.post("/api/system/update", headers=auth_headers)

    assert response.status_code == 202
    peticion = json.loads((channel / REQUEST_FILE).read_text(encoding="utf-8"))
    assert peticion["requested_by"] == "admin"
    # La petición NO puede dirigir a dónde actualizar: el anfitrión lo decide.
    assert set(peticion) == {"requested_by", "at"}
    assert (channel / STATUS_FILE).exists()


async def test_update_is_refused_for_non_admins(make_user, channel) -> None:
    headers = make_user("op-a", role="operador", group_name="EmpresaA")

    async with client() as api:
        response = await api.post("/api/system/update", headers=headers)

    assert response.status_code == 403
    assert not (channel / REQUEST_FILE).exists()


async def test_second_request_while_running_is_rejected(
    auth_headers, channel
) -> None:
    async with client() as api:
        first = await api.post("/api/system/update", headers=auth_headers)
        second = await api.post("/api/system/update", headers=auth_headers)

    assert first.status_code == 202
    assert second.status_code == 409


async def test_update_without_channel_reports_it(auth_headers, tmp_path) -> None:
    settings = get_settings()
    original = settings.update_channel_dir
    settings.update_channel_dir = str(tmp_path / "sin-montar")
    try:
        async with client() as api:
            response = await api.post("/api/system/update", headers=auth_headers)
    finally:
        settings.update_channel_dir = original

    assert response.status_code == 503
    assert "no está montado" in response.json()["detail"]


async def test_status_reads_what_the_host_wrote(auth_headers, channel) -> None:
    (channel / STATUS_FILE).write_text(
        json.dumps(
            {
                "state": "ok",
                "detail": "Actualizado de aaaaaaa a bbbbbbb.",
                "at": "2026-09-13T18:00:00+00:00",
                "from": "aaaaaaa",
                "to": "bbbbbbb",
            }
        ),
        encoding="utf-8",
    )

    async with client() as api:
        response = await api.get("/api/system/update-status", headers=auth_headers)

    assert response.json()["state"] == "ok"
    assert response.json()["to"] == "bbbbbbb"


async def test_corrupt_status_does_not_break_the_panel(
    auth_headers, channel
) -> None:
    (channel / STATUS_FILE).write_text("esto no es json", encoding="utf-8")

    async with client() as api:
        response = await api.get("/api/system/update-status", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["state"] == "sin-datos"


def test_request_update_writes_status_before_the_request(tmp_path) -> None:
    """Si systemd dispara al instante, el panel no debe ver un hueco sin estado."""
    request_update(str(tmp_path), "admin")

    assert (tmp_path / STATUS_FILE).exists()
    estado = json.loads((tmp_path / STATUS_FILE).read_text(encoding="utf-8"))
    assert estado["state"] == "solicitada"
