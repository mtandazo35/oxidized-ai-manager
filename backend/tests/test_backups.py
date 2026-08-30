import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module


pytestmark = pytest.mark.anyio

TOKEN_HEADER = {"X-Oxidized-Token": "test-oxidized-token"}


def client() -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url="http://test")


async def post_event(api: AsyncClient, node: str, event: str, commit: str = ""):
    return await api.post(
        "/api/oxidized/events",
        headers=TOKEN_HEADER,
        json={"node": node, "event": event, "commit": commit},
    )


async def test_event_without_token_returns_401() -> None:
    async with client() as api:
        response = await api.post(
            "/api/oxidized/events",
            json={"node": "rb-lab-01", "event": "node_success"},
        )

    assert response.status_code == 401


async def test_event_invalid_type_returns_422() -> None:
    async with client() as api:
        response = await post_event(api, "rb-lab-01", "post_store")

    assert response.status_code == 422


async def test_event_is_recorded(backup_event_repository) -> None:
    async with client() as api:
        response = await post_event(api, "rb-lab-01", "node_success", "abc123")

    assert response.status_code == 204
    assert len(backup_event_repository._events) == 1
    assert backup_event_repository._events[0]["commit_ref"] == "abc123"


async def test_placeholder_events_are_ignored(backup_event_repository) -> None:
    async with client() as api:
        response = await post_event(api, "phase1-placeholder", "node_fail")

    assert response.status_code == 204
    assert backup_event_repository._events == []


async def test_status_requires_login() -> None:
    async with client() as api:
        response = await api.get("/api/backups/status")

    assert response.status_code == 401


async def test_status_aggregates_last_backup(auth_headers) -> None:
    async with client() as api:
        await post_event(api, "rb-lab-01", "node_success", "abc123")
        await post_event(api, "rb-lab-01", "node_fail")
        await post_event(api, "rb-lab-02", "node_success", "def456")
        response = await api.get("/api/backups/status", headers=auth_headers)

    assert response.status_code == 200
    status = {entry["node"]: entry for entry in response.json()}
    assert status["rb-lab-01"]["last_event"] == "node_fail"
    assert status["rb-lab-01"]["last_commit"] == "abc123"
    assert status["rb-lab-01"]["last_success_at"] is not None
    assert status["rb-lab-02"]["last_event"] == "node_success"


async def test_reload_requires_login() -> None:
    async with client() as api:
        response = await api.post("/api/oxidized/reload")

    assert response.status_code == 401


async def test_reload_reports_unreachable_oxidized(auth_headers) -> None:
    async with client() as api:
        response = await api.post("/api/oxidized/reload", headers=auth_headers)

    assert response.status_code == 502


async def test_events_filter_by_node(auth_headers) -> None:
    async with client() as api:
        await post_event(api, "rb-lab-01", "node_success", "abc123")
        await post_event(api, "rb-lab-02", "node_success", "def456")
        response = await api.get(
            "/api/backups/events",
            params={"node": "rb-lab-02"},
            headers=auth_headers,
        )

    assert response.status_code == 200
    events = response.json()
    assert len(events) == 1
    assert events[0]["node"] == "rb-lab-02"
    assert events[0]["commit_ref"] == "def456"
