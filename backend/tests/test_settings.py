import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.scheduler import mask_remote_url


pytestmark = pytest.mark.anyio


def client() -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_settings_require_login() -> None:
    async with client() as api:
        response = await api.get("/api/settings")

    assert response.status_code == 401


async def test_defaults(auth_headers) -> None:
    async with client() as api:
        response = await api.get("/api/settings", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["backup_interval_minutes"] == 60
    assert body["git_remote_enabled"] is False
    assert body["git_remote_url"] == ""
    assert body["git_push_interval_minutes"] == 60
    assert body["last_push_ok"] is None


async def test_push_interval_saved(auth_headers) -> None:
    async with client() as api:
        response = await api.put(
            "/api/settings",
            headers=auth_headers,
            json={
                "backup_interval_minutes": 60,
                "git_remote_enabled": False,
                "git_remote_url": "",
                "git_push_interval_minutes": 240,
            },
        )

    assert response.status_code == 200
    assert response.json()["git_push_interval_minutes"] == 240


async def test_update_and_mask_credentials(auth_headers) -> None:
    async with client() as api:
        updated = await api.put(
            "/api/settings",
            headers=auth_headers,
            json={
                "backup_interval_minutes": 30,
                "git_remote_enabled": True,
                "git_remote_url": "https://user:sekrit@github.com/u/backups.git",
            },
        )

    assert updated.status_code == 200
    body = updated.json()
    assert body["backup_interval_minutes"] == 30
    assert body["git_remote_enabled"] is True
    assert "sekrit" not in body["git_remote_url"]
    assert body["git_remote_url"] == "https://user:***@github.com/u/backups.git"


async def test_masked_url_keeps_stored_secret(
    auth_headers, settings_repository
) -> None:
    async with client() as api:
        await api.put(
            "/api/settings",
            headers=auth_headers,
            json={
                "backup_interval_minutes": 30,
                "git_remote_enabled": True,
                "git_remote_url": "https://user:sekrit@github.com/u/backups.git",
            },
        )
        second = await api.put(
            "/api/settings",
            headers=auth_headers,
            json={
                "backup_interval_minutes": 15,
                "git_remote_enabled": True,
                "git_remote_url": "https://user:***@github.com/u/backups.git",
            },
        )

    assert second.status_code == 200
    stored = await settings_repository.get_all()
    assert stored["git_remote_url"] == "https://user:sekrit@github.com/u/backups.git"
    assert stored["backup_interval_minutes"] == "15"


async def test_invalid_url_rejected(auth_headers) -> None:
    async with client() as api:
        response = await api.put(
            "/api/settings",
            headers=auth_headers,
            json={
                "backup_interval_minutes": 60,
                "git_remote_enabled": False,
                "git_remote_url": "ftp://malo",
            },
        )

    assert response.status_code == 422


async def test_enable_push_without_url_rejected(auth_headers) -> None:
    async with client() as api:
        response = await api.put(
            "/api/settings",
            headers=auth_headers,
            json={
                "backup_interval_minutes": 60,
                "git_remote_enabled": True,
                "git_remote_url": "",
            },
        )

    assert response.status_code == 422


async def test_interval_out_of_range_rejected(auth_headers) -> None:
    async with client() as api:
        response = await api.put(
            "/api/settings",
            headers=auth_headers,
            json={
                "backup_interval_minutes": 1,
                "git_remote_enabled": False,
                "git_remote_url": "",
            },
        )

    assert response.status_code == 422


def test_mask_remote_url_variants() -> None:
    assert mask_remote_url("https://u:tok@host/r.git") == "https://u:***@host/r.git"
    assert mask_remote_url("https://host/r.git") == "https://host/r.git"
    assert mask_remote_url("") == ""


def test_mask_remote_url_hides_bare_tokens() -> None:
    """Regresión: `https://TOKEN@host` (sin usuario) salía sin ocultar."""
    assert (
        mask_remote_url("https://ghp_abc123@github.com/u/r.git")
        == "https://***@github.com/u/r.git"
    )
    assert mask_remote_url("ssh://git@host/r.git") == "ssh://git@host/r.git"
    assert mask_remote_url("git@github.com:u/r.git") == "git@github.com:u/r.git"


async def test_telegram_token_se_guarda_cifrado_y_enmascarado(
    auth_headers, settings_repository
) -> None:
    async with client() as api:
        updated = await api.put(
            "/api/settings",
            headers=auth_headers,
            json={
                "backup_interval_minutes": 60,
                "git_remote_enabled": False,
                "git_remote_url": "",
                "telegram_enabled": True,
                "telegram_bot_token": "123:ABCsecreto",
                "telegram_chat_id": "-100999",
                "stale_alert_hour": 7,
                "stale_alert_threshold_hours": 12,
            },
        )

    assert updated.status_code == 200
    body = updated.json()
    assert body["telegram_enabled"] is True
    assert body["telegram_bot_token"] == "***"
    assert body["telegram_chat_id"] == "-100999"
    assert body["stale_alert_hour"] == 7
    assert body["stale_alert_threshold_hours"] == 12
    stored = await settings_repository.get_all()
    assert stored["telegram_bot_token"] == "123:ABCsecreto"


async def test_telegram_masked_token_conserva_el_guardado(
    auth_headers, settings_repository
) -> None:
    await settings_repository.set_many({"telegram_bot_token": "123:ABCsecreto"})
    async with client() as api:
        response = await api.put(
            "/api/settings",
            headers=auth_headers,
            json={
                "backup_interval_minutes": 60,
                "git_remote_enabled": False,
                "git_remote_url": "",
                "telegram_enabled": True,
                "telegram_bot_token": "***",
                "telegram_chat_id": "-100999",
            },
        )

    assert response.status_code == 200
    stored = await settings_repository.get_all()
    assert stored["telegram_bot_token"] == "123:ABCsecreto"


async def test_test_git_sin_url(auth_headers) -> None:
    async with client() as api:
        response = await api.post("/api/settings/test-git", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"ok": False, "detail": "No hay una URL de Git configurada."}


async def test_test_git_hace_push(auth_headers, settings_repository, monkeypatch) -> None:
    await settings_repository.set_many(
        {"git_remote_url": "https://u:tok@github.com/u/r.git"}
    )

    async def fake_push(repo_path, url):
        return True, "ok"

    monkeypatch.setattr("app.settings_api.push_backups", fake_push)
    async with client() as api:
        response = await api.post("/api/settings/test-git", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "detail": "ok"}


async def test_telegram_activar_sin_token_o_chat_rechazado(auth_headers) -> None:
    async with client() as api:
        response = await api.put(
            "/api/settings",
            headers=auth_headers,
            json={
                "backup_interval_minutes": 60,
                "git_remote_enabled": False,
                "git_remote_url": "",
                "telegram_enabled": True,
                "telegram_bot_token": "",
                "telegram_chat_id": "",
            },
        )

    assert response.status_code == 422


async def test_http_remote_is_rejected(auth_headers) -> None:
    async with client() as api:
        response = await api.put(
            "/api/settings",
            headers=auth_headers,
            json={
                "backup_interval_minutes": 60,
                "git_remote_enabled": False,
                "git_remote_url": "http://user:tok@git.example.com/r.git",
            },
        )

    assert response.status_code == 422
    assert "https://" in response.json()["detail"]


async def test_masked_url_with_other_host_does_not_reuse_token(
    auth_headers, settings_repository
) -> None:
    """Regresión: bastaba con que la URL tuviera `:***@` para reutilizar el
    token guardado, aunque el servidor fuera otro."""
    await settings_repository.set_many(
        {"git_remote_url": "https://user:sekrit@github.com/u/backups.git"}
    )
    async with client() as api:
        response = await api.put(
            "/api/settings",
            headers=auth_headers,
            json={
                "backup_interval_minutes": 60,
                "git_remote_enabled": True,
                "git_remote_url": "https://user:***@atacante.example/u/backups.git",
            },
        )

    assert response.status_code == 422
    stored = await settings_repository.get_all()
    assert stored["git_remote_url"] == "https://user:sekrit@github.com/u/backups.git"


async def test_masked_bare_token_url_keeps_stored_secret(
    auth_headers, settings_repository
) -> None:
    await settings_repository.set_many(
        {"git_remote_url": "https://ghp_abc@github.com/u/backups.git"}
    )
    async with client() as api:
        response = await api.put(
            "/api/settings",
            headers=auth_headers,
            json={
                "backup_interval_minutes": 60,
                "git_remote_enabled": True,
                "git_remote_url": "https://***@github.com/u/backups.git",
            },
        )

    assert response.status_code == 200
    assert response.json()["git_remote_url"] == "https://***@github.com/u/backups.git"
    stored = await settings_repository.get_all()
    assert stored["git_remote_url"] == "https://ghp_abc@github.com/u/backups.git"
