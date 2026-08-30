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
