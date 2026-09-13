"""Bitácora y control de acceso por IP.

La IP de origen se simula con el parámetro `client` del transporte: por
defecto sería 127.0.0.1, que está exenta de bloqueo y no serviría para probar
nada.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.access import (
    invalid_entries,
    ip_in,
    is_allowed,
    is_never_blocked,
    parse_networks,
)


pytestmark = pytest.mark.anyio

ATACANTE = "203.0.113.9"
OFICINA = "10.99.99.25"


def client(ip: str = ATACANTE) -> AsyncClient:
    transport = ASGITransport(app=main_module.app, client=(ip, 54321))
    return AsyncClient(transport=transport, base_url="http://test")


async def login(api, username="admin", password="wrong"):
    return await api.post(
        "/api/auth/login", data={"username": username, "password": password}
    )


# --- Cálculo puro de IPs ---------------------------------------------------


def test_parse_networks_accepts_addresses_and_ranges() -> None:
    redes = parse_networks("10.0.0.5, 192.168.1.0/24\n2001:db8::/32")

    assert len(redes) == 3
    assert str(redes[0]) == "10.0.0.5/32"


def test_invalid_entries_are_reported() -> None:
    assert invalid_entries("10.0.0.0/24, no-es-una-ip, 8.8.8.8") == ["no-es-una-ip"]
    assert invalid_entries("10.0.0.0/24 8.8.8.8") == []


def test_ip_in_matches_ranges() -> None:
    assert ip_in("10.99.99.0/24", "10.99.99.25") is True
    assert ip_in("10.99.99.0/24", "10.99.98.25") is False
    assert ip_in("10.99.99.0/24", "no-es-ip") is False


def test_disabled_allowlist_lets_everyone_in() -> None:
    assert is_allowed("10.0.0.0/8", False, ATACANTE) is True


def test_enabled_but_empty_allowlist_fails_open() -> None:
    """Una lista vacía que negara todo dejaría el panel inaccesible."""
    assert is_allowed("", True, ATACANTE) is True
    assert is_allowed("   ", True, ATACANTE) is True


def test_enabled_allowlist_filters() -> None:
    assert is_allowed("10.99.99.0/24", True, OFICINA) is True
    assert is_allowed("10.99.99.0/24", True, ATACANTE) is False


def test_loopback_is_never_locked_out_nor_blocked() -> None:
    assert is_allowed("10.99.99.0/24", True, "127.0.0.1") is True
    assert is_never_blocked("127.0.0.1") is True
    assert is_never_blocked(ATACANTE) is False
    # Lo que el operador declaró de confianza tampoco se autobloquea.
    assert is_never_blocked(OFICINA, "10.99.99.0/24") is True


# --- Registro de accesos ---------------------------------------------------


async def test_successful_login_is_logged_with_its_ip(
    user_repository, activity_repository
) -> None:
    async with client(OFICINA) as api:
        response = await login(api, password="admin-test-password")

    assert response.status_code == 200
    entrada = activity_repository.entries[-1]
    assert entrada["action"] == "login.ok"
    assert entrada["ip"] == OFICINA
    assert entrada["username"] == "admin"
    assert entrada["ok"] is True


async def test_failed_login_is_logged(user_repository, activity_repository) -> None:
    async with client() as api:
        await login(api)

    entrada = activity_repository.entries[-1]
    assert entrada["action"] == "login.fail"
    assert entrada["ip"] == ATACANTE
    assert entrada["ok"] is False


async def test_the_log_never_stores_the_password(
    user_repository, activity_repository
) -> None:
    async with client() as api:
        await login(api, password="MiClaveSecreta123")

    assert "MiClaveSecreta123" not in repr(activity_repository.entries)


async def test_mutations_are_logged_by_the_middleware(
    auth_headers, activity_repository
) -> None:
    async with client(OFICINA) as api:
        await api.post(
            "/api/devices",
            headers=auth_headers,
            json={"name": "rb-log", "address": "192.0.2.1"},
        )

    entrada = activity_repository.entries[-1]
    assert entrada["action"] == "equipo.alta"
    assert entrada["username"] == "admin"
    assert entrada["ip"] == OFICINA
    assert entrada["ok"] is True


async def test_reads_are_not_logged(auth_headers, activity_repository) -> None:
    async with client() as api:
        await api.get("/api/devices", headers=auth_headers)

    assert activity_repository.entries == []


async def test_failed_mutation_is_logged_as_failure(
    make_user, activity_repository
) -> None:
    headers = make_user("lector-a", role="lector", group_name="EmpresaA")

    async with client() as api:
        await api.post(
            "/api/devices",
            headers=headers,
            json={"name": "rb-no", "address": "192.0.2.1"},
        )

    entrada = activity_repository.entries[-1]
    assert entrada["action"] == "equipo.alta"
    assert entrada["ok"] is False
    assert "403" in entrada["detail"]


# --- Bloqueos --------------------------------------------------------------


async def test_ip_is_blocked_after_repeated_failures(
    user_repository, access_repository, monkeypatch
) -> None:
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ip_block_threshold", 3)
    monkeypatch.setattr(settings, "account_lock_threshold", 99)
    monkeypatch.setattr("app.middleware.LOGIN_RATE_LIMIT", 100)

    async with client() as api:
        for _ in range(3):
            await login(api, username="cualquiera")
        bloqueada = await login(api, username="otra")

    assert bloqueada.status_code == 429
    assert ATACANTE in access_repository.ips


async def test_a_trusted_ip_is_never_auto_blocked(
    user_repository, access_repository, settings_repository, monkeypatch
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "ip_block_threshold", 2)
    monkeypatch.setattr(get_settings(), "account_lock_threshold", 99)
    monkeypatch.setattr("app.middleware.LOGIN_RATE_LIMIT", 100)
    await settings_repository.set_many({"login_allowlist": "10.99.99.0/24"})

    async with client(OFICINA) as api:
        for _ in range(4):
            await login(api, username="quien-sea")

    assert OFICINA not in access_repository.ips


async def test_allowlist_rejects_an_outside_address(
    user_repository, settings_repository, activity_repository
) -> None:
    await settings_repository.set_many(
        {"login_allowlist": "10.99.99.0/24", "login_allowlist_enabled": "true"}
    )

    async with client() as api:
        response = await login(api, password="admin-test-password")

    assert response.status_code == 403
    assert activity_repository.entries[-1]["action"] == "login.denied"


async def test_allowlist_lets_the_office_in(
    user_repository, settings_repository
) -> None:
    await settings_repository.set_many(
        {"login_allowlist": "10.99.99.0/24", "login_allowlist_enabled": "true"}
    )

    async with client(OFICINA) as api:
        response = await login(api, password="admin-test-password")

    assert response.status_code == 200


# --- API de consulta -------------------------------------------------------


@pytest.mark.parametrize("role", ["operador", "auditor", "lector"])
async def test_only_admin_sees_the_log(make_user, role) -> None:
    headers = make_user("cuenta", role=role, group_name="EmpresaA")

    async with client() as api:
        actividad = await api.get("/api/access/activity", headers=headers)
        bloqueos = await api.get("/api/access/blocks", headers=headers)

    assert actividad.status_code == 403
    assert bloqueos.status_code == 403


async def test_admin_reads_and_filters_the_log(
    auth_headers, activity_repository
) -> None:
    await activity_repository.record(action="login.ok", username="admin", ok=True)
    await activity_repository.record(action="login.fail", username="intruso", ok=False)

    async with client() as api:
        todo = await api.get("/api/access/activity", headers=auth_headers)
        fallos = await api.get(
            "/api/access/activity?only_failures=true", headers=auth_headers
        )

    assert len(todo.json()) >= 2
    assert all(entrada["ok"] is False for entrada in fallos.json())


async def test_admin_can_unblock_an_ip(auth_headers, access_repository) -> None:
    await access_repository.register_ip_failure(ATACANTE, 15, 1, 30)
    assert ATACANTE in access_repository.ips

    async with client() as api:
        response = await api.post(
            f"/api/access/unblock-ip?ip={ATACANTE}", headers=auth_headers
        )

    assert response.status_code == 204
    assert ATACANTE not in access_repository.ips


async def test_admin_can_unlock_an_account(auth_headers, access_repository) -> None:
    await access_repository.register_account_failure("admin", 15, 1, 30)

    async with client() as api:
        response = await api.post(
            "/api/access/unlock-account?username=admin", headers=auth_headers
        )

    assert response.status_code == 204
    assert "admin" not in access_repository.accounts


async def test_policy_rejects_unreadable_entries(auth_headers) -> None:
    async with client() as api:
        response = await api.put(
            "/api/access/policy",
            headers=auth_headers,
            json={"allowlist_enabled": False, "allowlist": "10.0.0.0/24, pepito"},
        )

    assert response.status_code == 422
    assert "pepito" in response.json()["detail"]


async def test_policy_refuses_to_lock_the_admin_out(auth_headers) -> None:
    """Activar la lista sin incluirse a uno mismo obligaría a entrar por SSH."""
    async with client(ATACANTE) as api:
        response = await api.put(
            "/api/access/policy",
            headers=auth_headers,
            json={"allowlist_enabled": True, "allowlist": "10.99.99.0/24"},
        )

    assert response.status_code == 422
    assert ATACANTE in response.json()["detail"]


async def test_policy_is_saved_when_the_admin_is_included(
    auth_headers, settings_repository
) -> None:
    async with client(OFICINA) as api:
        response = await api.put(
            "/api/access/policy",
            headers=auth_headers,
            json={"allowlist_enabled": True, "allowlist": "10.99.99.0/24"},
        )

    assert response.status_code == 200
    valores = await settings_repository.get_all()
    assert valores["login_allowlist_enabled"] == "true"
