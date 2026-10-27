"""Sesiones: revocación, cambio de clave obligatorio, lista de IPs permitidas
en toda la API, bloqueo por cuenta y enumeración de usuarios."""

import time

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.config import get_settings
from app.security import create_access_token, decode_access_claims
from tests.conftest import TEST_ADMIN_PASSWORD


pytestmark = pytest.mark.anyio

EXTERNA = "203.0.113.9"


def client(ip: str = "127.0.0.1") -> AsyncClient:
    transport = ASGITransport(app=main_module.app, client=(ip, 123))
    return AsyncClient(transport=transport, base_url="http://test")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def login(api: AsyncClient, username: str, password: str):
    return await api.post(
        "/api/auth/login", data={"username": username, "password": password}
    )


# --- 1. Revocación de sesiones ---------------------------------------------


async def test_password_change_revokes_old_tokens(auth_headers) -> None:
    async with client() as api:
        cambio = await api.post(
            "/api/auth/change-password",
            headers=auth_headers,
            json={"current_password": TEST_ADMIN_PASSWORD, "new_password": "otra-clave-9"},
        )
        viejo = await api.get("/api/auth/me", headers=auth_headers)
        nuevo = await api.get(
            "/api/auth/me", headers=bearer(cambio.json()["access_token"])
        )

    assert cambio.status_code == 200
    assert viejo.status_code == 401
    assert nuevo.status_code == 200


async def test_admin_password_reset_revokes_user_sessions(
    auth_headers, make_user
) -> None:
    headers = make_user("cliente", role="lector", group_name="acme")
    async with client() as api:
        antes = await api.get("/api/devices", headers=headers)
        reset = await api.patch(
            "/api/users/cliente", headers=auth_headers, json={"password": "nueva-clave-1"}
        )
        despues = await api.get("/api/devices", headers=headers)

    assert antes.status_code == 200
    assert reset.status_code == 200
    assert despues.status_code == 401


async def test_role_change_revokes_user_sessions(auth_headers, make_user) -> None:
    headers = make_user("op", role="operador", group_name="acme")
    async with client() as api:
        cambio = await api.patch(
            "/api/users/op", headers=auth_headers, json={"role": "lector"}
        )
        despues = await api.get("/api/devices", headers=headers)

    assert cambio.status_code == 200
    assert despues.status_code == 401


async def test_logout_revokes_every_session(
    user_repository, activity_repository
) -> None:
    async with client() as api:
        uno = (await login(api, "admin", TEST_ADMIN_PASSWORD)).json()["access_token"]
        dos = (await login(api, "admin", TEST_ADMIN_PASSWORD)).json()["access_token"]
        salida = await api.post("/api/auth/logout", headers=bearer(uno))
        con_uno = await api.get("/api/auth/me", headers=bearer(uno))
        con_dos = await api.get("/api/auth/me", headers=bearer(dos))
        otra_vez = await login(api, "admin", TEST_ADMIN_PASSWORD)

    assert salida.status_code == 204
    assert activity_repository.actions().count("logout") == 1
    assert con_uno.status_code == 401
    assert con_dos.status_code == 401
    assert otra_vez.status_code == 200


async def test_refresh_rejects_revoked_token_and_keeps_version(
    auth_headers, user_repository
) -> None:
    user_repository._users["admin"]["token_version"] = 3
    vigente = create_access_token("admin", get_settings().app_secret_key, 60, 3)
    async with client() as api:
        revocado = await api.post("/api/auth/refresh", headers=auth_headers)
        renovado = await api.post("/api/auth/refresh", headers=bearer(vigente))

    assert revocado.status_code == 401
    assert renovado.status_code == 200
    claims = decode_access_claims(
        renovado.json()["access_token"], get_settings().app_secret_key
    )
    assert claims is not None and claims.version == 3


async def test_token_without_version_counts_as_zero(user_repository) -> None:
    """Las sesiones abiertas antes del despliegue (sin `ver`) siguen valiendo."""
    import datetime as dt

    import jwt

    ahora = dt.datetime.now(dt.timezone.utc)
    antiguo = jwt.encode(
        {"sub": "admin", "iat": ahora, "exp": ahora + dt.timedelta(minutes=5)},
        get_settings().app_secret_key,
        algorithm="HS256",
    )
    async with client() as api:
        response = await api.get("/api/auth/me", headers=bearer(antiguo))

    assert response.status_code == 200


# --- 2. Cambio de clave obligatorio en el servidor --------------------------


async def test_forced_password_change_blocks_the_rest_of_the_api(
    auth_headers, user_repository
) -> None:
    user_repository._users["admin"]["must_change_password"] = True
    async with client() as api:
        devices = await api.get("/api/devices", headers=auth_headers)
        users = await api.get("/api/users", headers=auth_headers)
        me = await api.get("/api/auth/me", headers=auth_headers)
        refresh = await api.post("/api/auth/refresh", headers=auth_headers)
        cambio = await api.post(
            "/api/auth/change-password",
            headers=auth_headers,
            json={"current_password": TEST_ADMIN_PASSWORD, "new_password": "clave-nueva-1"},
        )
        nuevo = bearer(cambio.json()["access_token"])
        despues = await api.get("/api/devices", headers=nuevo)

    assert devices.status_code == 403
    assert "cambiar su clave" in devices.json()["detail"]
    assert users.status_code == 403
    assert me.status_code == 200
    assert refresh.status_code == 200
    assert cambio.status_code == 200
    assert despues.status_code == 200


# --- 5. Lista de IPs permitidas y bloqueo por cuenta -------------------------


async def test_allowlist_applies_to_every_authenticated_request(
    auth_headers, settings_repository
) -> None:
    await settings_repository.set_many(
        {"login_allowlist_enabled": "true", "login_allowlist": "10.0.0.0/24"}
    )
    async with client(EXTERNA) as api:
        fuera = await api.get("/api/devices", headers=auth_headers)
    async with client("10.0.0.7") as api:
        dentro = await api.get("/api/devices", headers=auth_headers)
    async with client("127.0.0.1") as api:
        local = await api.get("/api/devices", headers=auth_headers)

    assert fuera.status_code == 403
    assert "no está autorizada" in fuera.json()["detail"]
    assert dentro.status_code == 200
    assert local.status_code == 200


async def test_account_lock_does_not_apply_from_trusted_networks(
    settings_repository, access_repository, monkeypatch
) -> None:
    """Regresión: un atacante externo podía dejar fuera al administrador
    fallando su clave a propósito."""
    monkeypatch.setattr("app.middleware.LOGIN_RATE_LIMIT", 100)
    await settings_repository.set_many(
        {"login_allowlist_enabled": "true", "login_allowlist": "10.0.0.0/24"}
    )
    # La lista activa impide entrar desde fuera, así que el ataque viene de
    # una IP permitida pero se simula el bloqueo ya registrado.
    for _ in range(8):
        await access_repository.register_account_failure("admin", 15, 8, 15)
    assert await access_repository.account_lock("admin")

    async with client("10.0.0.7") as api:
        permitida = await login(api, "admin", TEST_ADMIN_PASSWORD)

    assert permitida.status_code == 200


async def test_account_lock_still_applies_from_untrusted_ip(
    access_repository, monkeypatch
) -> None:
    monkeypatch.setattr("app.middleware.LOGIN_RATE_LIMIT", 100)
    for _ in range(8):
        await access_repository.register_account_failure("admin", 15, 8, 15)

    async with client(EXTERNA) as api:
        externa = await login(api, "admin", TEST_ADMIN_PASSWORD)
    async with client("127.0.0.1") as api:
        local = await login(api, "admin", TEST_ADMIN_PASSWORD)

    assert externa.status_code == 429
    assert local.status_code == 200


# --- 6. Enumeración de usuarios ---------------------------------------------


async def test_unknown_user_costs_a_bcrypt_check(monkeypatch) -> None:
    llamadas: list[str] = []
    import app.auth as auth_module

    original = auth_module.burn_password_check

    def espia(password: str) -> None:
        llamadas.append(password)
        original(password)

    monkeypatch.setattr(auth_module, "burn_password_check", espia)
    async with client() as api:
        inicio = time.perf_counter()
        response = await login(api, "no-existe", "lo-que-sea")
        transcurrido = time.perf_counter() - inicio

    assert response.status_code == 401
    assert response.json()["detail"] == "Usuario o clave incorrectos."
    assert llamadas == ["lo-que-sea"]
    # bcrypt con el coste por defecto tarda decenas de milisegundos.
    assert transcurrido > 0.01


async def test_overlong_username_is_rejected_and_not_recorded(
    access_repository,
) -> None:
    async with client(EXTERNA) as api:
        response = await login(api, "x" * 129, "lo-que-sea")

    assert response.status_code == 401
    assert access_repository.accounts == {}
