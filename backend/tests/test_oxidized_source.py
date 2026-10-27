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
            "ssh_port": 22,
            "model": "routeros",
            "username": "",
            "password": "",
        }
    ]


async def test_returns_enabled_devices_with_credentials(auth_headers) -> None:
    async with client() as api:
        await api.post(
            "/api/devices",
            headers=auth_headers,
            json={
                "name": "rb-lab-01",
                "address": "192.0.2.10",
                "port": 9922,
                "username": "backup",
                "password": "s3cret",
            },
        )
        await api.post(
            "/api/devices",
            headers=auth_headers,
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
    assert nodes[0]["ssh_port"] == 9922
    assert nodes[0]["username"] == "backup"
    assert nodes[0]["password"] == "s3cret"


async def test_non_ascii_token_returns_401_not_500() -> None:
    """Regresión: `compare_digest` con un str no ASCII lanzaba TypeError."""
    async with client() as api:
        response = await api.get(
            "/api/oxidized/nodes",
            headers=[("X-Oxidized-Token", "contraseña".encode("latin-1"))],
        )

    assert response.status_code == 401


async def test_event_with_invalid_node_name_is_rejected() -> None:
    async with client() as api:
        response = await api.post(
            "/api/oxidized/events",
            headers=TOKEN_HEADER,
            json={"node": "../../etc/passwd", "event": "node_success"},
        )

    assert response.status_code == 422


async def test_unreadable_device_passwords_return_503(
    device_repository, monkeypatch
) -> None:
    """Con la APP_SECRET_KEY equivocada no se entrega el cifrado como clave."""
    from app.secrets_box import ClaveIlegibleError

    async def ilegible():
        raise ClaveIlegibleError("otra llave")

    monkeypatch.setattr(device_repository, "list_oxidized_nodes", ilegible)
    async with client() as api:
        response = await api.get("/api/oxidized/nodes", headers=TOKEN_HEADER)

    assert response.status_code == 503
    assert "APP_SECRET_KEY" in response.json()["detail"]


@pytest.mark.anyio
async def test_integridad_compara_solo_con_el_historial_del_equipo_actual(
    monkeypatch, device_repository, backup_integrity_repository
) -> None:
    """Con un nombre reutilizado, el primer respaldo se comparaba con la
    configuración del dueño anterior y salían avisos falsos («se encogió»)."""
    import datetime as dt
    from types import SimpleNamespace

    from app import oxidized_source

    desde = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
    await device_repository.create_device(
        {"name": "rb-reusado", "address": "192.0.2.9", "model": "routeros", "history_since": desde}
    )
    llamadas = []

    async def versiones_falsas(repo, node, limit, since=None):
        llamadas.append(("versions", since))
        return [{"commit": "a" * 40}]

    async def config_falsa(repo, node, commit, since=None):
        llamadas.append(("config", since))
        return ""

    monkeypatch.setattr(oxidized_source, "list_versions", versiones_falsas)
    monkeypatch.setattr(oxidized_source, "show_config", config_falsa)
    request = SimpleNamespace(app=main_module.app)

    await oxidized_source._revisar_integridad(request, "rb-reusado", "/system identity\nset name=x\n")

    assert llamadas and llamadas[0] == ("versions", desde)
