import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from tests.conftest import TEST_ADMIN_PASSWORD


pytestmark = pytest.mark.anyio


def client() -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url="http://test")


async def login(api: AsyncClient, username: str, password: str):
    return await api.post(
        "/api/auth/login", data={"username": username, "password": password}
    )


async def test_login_returns_token() -> None:
    async with client() as api:
        response = await login(api, "admin", TEST_ADMIN_PASSWORD)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


async def test_login_wrong_password_returns_401() -> None:
    async with client() as api:
        response = await login(api, "admin", "wrong")

    assert response.status_code == 401


async def test_login_unknown_user_returns_401() -> None:
    async with client() as api:
        response = await login(api, "nobody", TEST_ADMIN_PASSWORD)

    assert response.status_code == 401


async def test_me_requires_token(auth_headers) -> None:
    async with client() as api:
        anonymous = await api.get("/api/auth/me")
        identified = await api.get("/api/auth/me", headers=auth_headers)

    assert anonymous.status_code == 401
    assert identified.status_code == 200
    assert identified.json()["username"] == "admin"
    assert identified.json()["must_change_password"] is False


async def test_me_flags_forced_change(auth_headers, user_repository) -> None:
    user_repository._users["admin"]["must_change_password"] = True
    async with client() as api:
        response = await api.get("/api/auth/me", headers=auth_headers)

    assert response.json()["must_change_password"] is True


async def test_change_password_clears_forced_flag(auth_headers, user_repository) -> None:
    user_repository._users["admin"]["must_change_password"] = True
    async with client() as api:
        changed = await api.post(
            "/api/auth/change-password",
            headers=auth_headers,
            json={"current_password": TEST_ADMIN_PASSWORD, "new_password": "clave-nueva-1"},
        )
        me = await api.get("/api/auth/me", headers=auth_headers)

    assert changed.status_code == 204
    assert me.json()["must_change_password"] is False


async def test_devices_require_authentication() -> None:
    async with client() as api:
        response = await api.get("/api/devices")

    assert response.status_code == 401


async def test_invalid_token_is_rejected() -> None:
    async with client() as api:
        response = await api.get(
            "/api/devices", headers={"Authorization": "Bearer garbage"}
        )

    assert response.status_code == 401


async def test_change_password_flow(auth_headers) -> None:
    async with client() as api:
        changed = await api.post(
            "/api/auth/change-password",
            headers=auth_headers,
            json={
                "current_password": TEST_ADMIN_PASSWORD,
                "new_password": "new-password-123",
            },
        )
        old_login = await login(api, "admin", TEST_ADMIN_PASSWORD)
        new_login = await login(api, "admin", "new-password-123")

    assert changed.status_code == 204
    assert old_login.status_code == 401
    assert new_login.status_code == 200


async def test_change_password_wrong_current_returns_403(auth_headers) -> None:
    async with client() as api:
        response = await api.post(
            "/api/auth/change-password",
            headers=auth_headers,
            json={
                "current_password": "wrong",
                "new_password": "new-password-123",
            },
        )

    assert response.status_code == 403


async def test_change_password_rejects_short_password(auth_headers) -> None:
    async with client() as api:
        response = await api.post(
            "/api/auth/change-password",
            headers=auth_headers,
            json={"current_password": TEST_ADMIN_PASSWORD, "new_password": "short"},
        )

    assert response.status_code == 422


# --- Sesión por inactividad -----------------------------------------------


async def test_refresh_extends_the_session(auth_headers) -> None:
    """Mientras hay actividad el panel renueva; sin ella el token caduca solo."""
    async with client() as api:
        response = await api.post("/api/auth/refresh", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["token_type"] == "bearer"


async def test_refresh_needs_a_valid_session() -> None:
    async with client() as api:
        response = await api.post("/api/auth/refresh")

    assert response.status_code == 401


async def test_refresh_gives_a_token_that_works(user_repository) -> None:
    from app.config import get_settings
    from app.security import create_access_token, decode_access_token

    viejo = create_access_token("admin", get_settings().app_secret_key, 60)
    async with client() as api:
        nuevo = await api.post(
            "/api/auth/refresh", headers={"Authorization": f"Bearer {viejo}"}
        )
        usable = await api.get(
            "/api/devices",
            headers={"Authorization": f"Bearer {nuevo.json()['access_token']}"},
        )

    assert usable.status_code == 200
    assert decode_access_token(
        nuevo.json()["access_token"], get_settings().app_secret_key
    ) == "admin"


async def test_me_tells_the_panel_the_idle_window(auth_headers) -> None:
    async with client() as api:
        response = await api.get("/api/auth/me", headers=auth_headers)

    assert response.json()["idle_minutes"] == 60
