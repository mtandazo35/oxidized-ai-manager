"""Aislamiento entre empresas y permisos por rol.

Es la prueba que importa del multi-tenant: si un endpoint se olvida del
filtro, un cliente vería los respaldos de otro. Por eso se recorre **cada**
endpoint que devuelve equipos, respaldos o auditoría, no una muestra.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module


pytestmark = pytest.mark.anyio


def client() -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def inventory(auth_headers):
    """Dos equipos de dos empresas distintas, creados por el admin."""
    async with client() as api:
        a = await api.post(
            "/api/devices",
            headers=auth_headers,
            json={"name": "rb-alfa", "address": "192.0.2.1", "group_name": "EmpresaA"},
        )
        b = await api.post(
            "/api/devices",
            headers=auth_headers,
            json={"name": "rb-beta", "address": "192.0.2.2", "group_name": "EmpresaB"},
        )
    return {"alfa": a.json(), "beta": b.json()}


# --- Visibilidad del inventario -------------------------------------------


async def test_client_only_sees_its_own_devices(inventory, make_user) -> None:
    headers = make_user("cliente-a", role="lector", group_name="EmpresaA")

    async with client() as api:
        response = await api.get("/api/devices", headers=headers)

    names = [device["name"] for device in response.json()]
    assert names == ["rb-alfa"]


async def test_admin_sees_every_company(inventory, auth_headers) -> None:
    async with client() as api:
        response = await api.get("/api/devices", headers=auth_headers)

    assert {device["name"] for device in response.json()} == {"rb-alfa", "rb-beta"}


async def test_device_of_another_company_is_not_found(inventory, make_user) -> None:
    headers = make_user("cliente-a", role="lector", group_name="EmpresaA")

    async with client() as api:
        response = await api.get(
            f"/api/devices/{inventory['beta']['id']}", headers=headers
        )

    # 404 y no 403: un 403 confirmaría que ese equipo existe.
    assert response.status_code == 404


async def test_account_without_company_sees_nothing(inventory, make_user) -> None:
    """Falla cerrada: una cuenta no admin sin empresa no ve todo, ve nada."""
    headers = make_user("huerfano", role="operador", group_name="")

    async with client() as api:
        response = await api.get("/api/devices", headers=headers)

    assert response.json() == []


# --- Escritura -------------------------------------------------------------


async def test_reader_cannot_create_devices(make_user) -> None:
    headers = make_user("cliente-a", role="lector", group_name="EmpresaA")

    async with client() as api:
        response = await api.post(
            "/api/devices",
            headers=headers,
            json={"name": "rb-nuevo", "address": "192.0.2.9"},
        )

    assert response.status_code == 403


async def test_operator_creation_is_forced_into_its_own_company(make_user) -> None:
    headers = make_user("op-a", role="operador", group_name="EmpresaA")

    async with client() as api:
        response = await api.post(
            "/api/devices",
            headers=headers,
            json={"name": "rb-propio", "address": "192.0.2.9"},
        )

    assert response.status_code == 201
    assert response.json()["group_name"] == "EmpresaA"


async def test_operator_cannot_create_in_another_company(make_user) -> None:
    headers = make_user("op-a", role="operador", group_name="EmpresaA")

    async with client() as api:
        response = await api.post(
            "/api/devices",
            headers=headers,
            json={
                "name": "rb-ajeno",
                "address": "192.0.2.9",
                "group_name": "EmpresaB",
            },
        )

    assert response.status_code == 403


async def test_operator_cannot_move_a_device_to_another_company(
    inventory, make_user
) -> None:
    headers = make_user("op-a", role="operador", group_name="EmpresaA")

    async with client() as api:
        response = await api.patch(
            f"/api/devices/{inventory['alfa']['id']}",
            headers=headers,
            json={"group_name": "EmpresaB"},
        )

    assert response.status_code == 403


async def test_operator_cannot_edit_or_delete_another_company(
    inventory, make_user
) -> None:
    headers = make_user("op-a", role="operador", group_name="EmpresaA")
    beta = inventory["beta"]["id"]

    async with client() as api:
        patched = await api.patch(
            f"/api/devices/{beta}", headers=headers, json={"address": "10.0.0.1"}
        )
        deleted = await api.delete(f"/api/devices/{beta}", headers=headers)
        backup = await api.post(f"/api/devices/{beta}/backup", headers=headers)

    assert patched.status_code == 404
    assert deleted.status_code == 404
    assert backup.status_code == 404


async def test_bulk_import_is_forced_into_the_operator_company(make_user) -> None:
    headers = make_user("op-a", role="operador", group_name="EmpresaA")

    async with client() as api:
        result = await api.post(
            "/api/devices/import",
            headers=headers,
            json={"text": "rb-importado,192.0.2.30,22,u,c,EmpresaB"},
        )
        listed = await api.get("/api/devices", headers=headers)

    # La línea trae otra empresa: se rechaza esa fila, no se reasigna en silencio.
    assert result.status_code == 403
    assert listed.json() == []


# --- Respaldos -------------------------------------------------------------


async def test_backup_status_and_events_are_filtered(
    inventory, make_user, backup_event_repository
) -> None:
    await backup_event_repository.record_event("rb-alfa", "node_success", "aaa")
    await backup_event_repository.record_event("rb-beta", "node_success", "bbb")
    headers = make_user("cliente-a", role="lector", group_name="EmpresaA")

    async with client() as api:
        status_response = await api.get("/api/backups/status", headers=headers)
        events_response = await api.get("/api/backups/events", headers=headers)

    assert [row["node"] for row in status_response.json()] == ["rb-alfa"]
    assert {row["node"] for row in events_response.json()} == {"rb-alfa"}


async def test_events_of_another_company_are_not_found(inventory, make_user) -> None:
    headers = make_user("cliente-a", role="lector", group_name="EmpresaA")

    async with client() as api:
        response = await api.get("/api/backups/events?node=rb-beta", headers=headers)

    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/backups/versions?node=rb-beta",
        "/api/backups/diff?node=rb-beta&commit=abc123",
        "/api/backups/config?node=rb-beta&commit=abc123",
    ],
)
async def test_git_endpoints_reject_another_company(
    inventory, make_user, path
) -> None:
    headers = make_user("cliente-a", role="lector", group_name="EmpresaA")

    async with client() as api:
        response = await api.get(path, headers=headers)

    assert response.status_code == 404


async def test_bulk_backup_scope_stays_inside_the_company(
    inventory, make_user, monkeypatch
) -> None:
    encolados = []

    async def fake_trigger(url, node):
        encolados.append(node)

    monkeypatch.setattr("app.backups.trigger_node_backup", fake_trigger)
    headers = make_user("op-a", role="operador", group_name="EmpresaA")

    async with client() as api:
        response = await api.post(
            "/api/backups/run", headers=headers, json={"scope": "all"}
        )

    assert response.status_code == 202
    assert encolados == ["rb-alfa"]


async def test_bulk_backup_by_ids_ignores_other_companies(
    inventory, make_user, monkeypatch
) -> None:
    encolados = []

    async def fake_trigger(url, node):
        encolados.append(node)

    monkeypatch.setattr("app.backups.trigger_node_backup", fake_trigger)
    headers = make_user("op-a", role="operador", group_name="EmpresaA")

    async with client() as api:
        response = await api.post(
            "/api/backups/run",
            headers=headers,
            json={
                "scope": "devices",
                "device_ids": [inventory["alfa"]["id"], inventory["beta"]["id"]],
            },
        )

    assert response.status_code == 202
    assert encolados == ["rb-alfa"]


async def test_reader_cannot_launch_backups(inventory, make_user) -> None:
    headers = make_user("cliente-a", role="lector", group_name="EmpresaA")

    async with client() as api:
        response = await api.post(
            "/api/backups/run", headers=headers, json={"scope": "all"}
        )

    assert response.status_code == 403


# --- Auditoría -------------------------------------------------------------


async def test_reader_has_no_access_to_the_audit(inventory, make_user) -> None:
    headers = make_user("cliente-a", role="lector", group_name="EmpresaA")

    async with client() as api:
        summary = await api.get("/api/audit/summary", headers=headers)
        node = await api.get("/api/audit/node?node=rb-alfa", headers=headers)

    assert summary.status_code == 403
    assert node.status_code == 403


async def test_auditor_only_audits_its_own_company(inventory, make_user) -> None:
    headers = make_user("auditor-a", role="auditor", group_name="EmpresaA")

    async with client() as api:
        summary = await api.get("/api/audit/summary", headers=headers)
        ajeno = await api.get("/api/audit/node?node=rb-beta", headers=headers)

    assert [row["node"] for row in summary.json()] == ["rb-alfa"]
    assert ajeno.status_code == 404


# --- Ajustes globales y cuentas -------------------------------------------


@pytest.mark.parametrize("role", ["operador", "auditor", "lector"])
async def test_only_admin_touches_global_settings(make_user, role) -> None:
    headers = make_user("cuenta", role=role, group_name="EmpresaA")

    async with client() as api:
        read = await api.get("/api/settings", headers=headers)
        reload = await api.post("/api/oxidized/reload", headers=headers)
        users = await api.get("/api/users", headers=headers)

    assert read.status_code == 403
    assert reload.status_code == 403
    assert users.status_code == 403


async def test_admin_can_manage_accounts(auth_headers) -> None:
    async with client() as api:
        created = await api.post(
            "/api/users",
            headers=auth_headers,
            json={
                "username": "cliente-b",
                "password": "clave-larga-123",
                "role": "lector",
                "group_name": "EmpresaB",
            },
        )
        listed = await api.get("/api/users", headers=auth_headers)

    assert created.status_code == 201
    assert created.json()["group_name"] == "EmpresaB"
    # La respuesta no devuelve ni la clave ni su hash.
    assert "password_hash" not in created.text
    assert "clave-larga-123" not in created.text
    assert {user["username"] for user in listed.json()} == {"admin", "cliente-b"}


async def test_non_admin_account_requires_a_company(auth_headers) -> None:
    async with client() as api:
        response = await api.post(
            "/api/users",
            headers=auth_headers,
            json={
                "username": "sin-empresa",
                "password": "clave-larga-123",
                "role": "lector",
            },
        )

    assert response.status_code == 422


async def test_duplicate_username_is_rejected(auth_headers) -> None:
    async with client() as api:
        response = await api.post(
            "/api/users",
            headers=auth_headers,
            json={
                "username": "admin",
                "password": "clave-larga-123",
                "role": "admin",
            },
        )

    assert response.status_code == 409


async def test_the_last_admin_cannot_be_removed_or_demoted(auth_headers) -> None:
    async with client() as api:
        demoted = await api.patch(
            "/api/users/admin", headers=auth_headers, json={"role": "lector"}
        )
        deleted = await api.delete("/api/users/admin", headers=auth_headers)

    assert demoted.status_code == 409
    assert deleted.status_code == 409


async def test_me_reports_role_and_company(make_user) -> None:
    headers = make_user("cliente-a", role="auditor", group_name="EmpresaA")

    async with client() as api:
        response = await api.get("/api/auth/me", headers=headers)

    body = response.json()
    assert body["role"] == "auditor"
    assert body["group_name"] == "EmpresaA"
    assert body["can_audit"] is True
    assert body["can_write"] is False
    assert body["is_admin"] is False
