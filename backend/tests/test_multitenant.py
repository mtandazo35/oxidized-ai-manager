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


# --- Tabla de permisos por rol --------------------------------------------


async def test_role_matrix_is_admin_only(make_user) -> None:
    headers = make_user("op-a", role="operador", group_name="EmpresaA")

    async with client() as api:
        response = await api.get("/api/users/roles", headers=headers)

    assert response.status_code == 403


async def test_role_matrix_lists_every_role_and_capability(auth_headers) -> None:
    from app.schemas import ROLES
    from app.users_api import CAPABILITIES

    async with client() as api:
        response = await api.get("/api/users/roles", headers=auth_headers)

    body = response.json()
    assert [r["name"] for r in body["roles"]] == list(ROLES)
    assert len(body["capabilities"]) == len(CAPABILITIES)
    assert all(r["description"] for r in body["roles"])


async def test_role_matrix_matches_the_real_permissions(auth_headers) -> None:
    """La tabla no puede prometer algo que la API luego niegue."""
    from app.auth import CurrentUser

    async with client() as api:
        body = (await api.get("/api/users/roles", headers=auth_headers)).json()

    por_etiqueta = {c["label"]: c["grants"] for c in body["capabilities"]}
    for role in ("admin", "operador", "auditor", "lector"):
        usuario = CurrentUser(username="x", role=role, group_name="EmpresaA")
        assert (
            por_etiqueta["Ver la auditoría de configuraciones"][role]
            == usuario.can_audit()
        )
        assert (
            por_etiqueta["Dar de alta, editar y borrar equipos"][role]
            == usuario.can_write()
        )
        assert por_etiqueta["Crear y gestionar cuentas"][role] == usuario.is_admin


async def test_only_admin_sees_every_company_in_the_matrix(auth_headers) -> None:
    async with client() as api:
        body = (await api.get("/api/users/roles", headers=auth_headers)).json()

    alcance = {r["name"]: r["scope"] for r in body["roles"]}
    assert alcance["admin"] == "todas las empresas"
    assert alcance["lector"] == "solo su empresa"


# --- Reutilización de nombres entre empresas -------------------------------
#
# El historial git, `backup_events` y `backup_integrity` se indexan por nombre
# de nodo. Si un nombre pasa de un equipo a otro (borrado y alta, cambio de
# nombre o cambio de empresa), lo anterior es del dueño previo y no puede
# verlo el actual: ahí estaban las claves de los routers de otra empresa.


import datetime as _dt  # noqa: E402
import os as _os  # noqa: E402
import subprocess as _subprocess  # noqa: E402

from app.config import get_settings as _get_settings  # noqa: E402

SECRETO_A = "ClaveSecretaDeEmpresaA"


@pytest.fixture
def shared_repo(tmp_path):
    """Repositorio de respaldos compartido, como el de Oxidized."""
    repo = str(tmp_path / "backups")
    _subprocess.run(["git", "init", "-q", repo], check=True, capture_output=True)
    original = _get_settings().oxidized_backup_repo
    _get_settings().oxidized_backup_repo = repo

    def commit(node: str, content: str, offset_seconds: int) -> str:
        """Respalda `node` con fecha relativa a ahora; devuelve el commit."""
        (tmp_path / "backups" / node).write_text(content)
        fecha = int(_dt.datetime.now(_dt.timezone.utc).timestamp()) + offset_seconds
        env = {
            **_os.environ,
            "GIT_AUTHOR_DATE": f"@{fecha} +0000",
            "GIT_COMMITTER_DATE": f"@{fecha} +0000",
        }
        base = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"]
        _subprocess.run([*base, "add", node], check=True, capture_output=True, env=env)
        _subprocess.run(
            [*base, "commit", "-q", "-m", f"update /{node}"],
            check=True,
            capture_output=True,
            env=env,
        )
        return _subprocess.run(
            [*base, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()

    yield commit
    _get_settings().oxidized_backup_repo = original


def _export_de(empresa: str, secreto: str) -> str:
    return (
        "# oct/01/2026 08:00:00 by RouterOS 7.23.2\n"
        "/system identity\n"
        f"set name=router-{empresa}\n"
        "/user\n"
        f'add name=respaldo group=full password="{secreto}"\n'
    )


def _envejecer_eventos(backup_event_repository, node: str) -> None:
    """Los eventos del dueño anterior son, por definición, del pasado."""
    for evento in backup_event_repository._events:
        if evento["node"] == node:
            evento["created_at"] -= _dt.timedelta(minutes=10)


async def _nada_del_dueno_anterior(
    api, node: str, commit_ajeno: str, lector: dict
) -> dict:
    """Recorre todo lo que expone historial de un nodo y lo devuelve."""
    respuestas = {
        "versions": await api.get(
            "/api/backups/versions", params={"node": node}, headers=lector
        ),
        "config_ajena": await api.get(
            "/api/backups/config",
            params={"node": node, "commit": commit_ajeno},
            headers=lector,
        ),
        "diff_ajeno": await api.get(
            "/api/backups/diff",
            params={"node": node, "commit": commit_ajeno},
            headers=lector,
        ),
        "events": await api.get(
            "/api/backups/events", params={"node": node}, headers=lector
        ),
        "status": await api.get("/api/backups/status", headers=lector),
    }
    assert respuestas["versions"].status_code == 200
    assert respuestas["config_ajena"].status_code == 404
    assert respuestas["diff_ajeno"].status_code == 404
    assert respuestas["events"].status_code == 200
    for nombre, respuesta in respuestas.items():
        assert SECRETO_A not in respuesta.text, nombre
        assert "router-a" not in respuesta.text, nombre
        if nombre not in ("config_ajena", "diff_ajeno"):
            # En los 404 el hash es el que mandó el propio cliente.
            assert commit_ajeno not in respuesta.text, nombre
    return respuestas


async def test_a_reused_name_does_not_inherit_another_company_history(
    auth_headers, make_user, shared_repo, backup_event_repository,
    backup_integrity_repository,
) -> None:
    op_a = make_user("op-a", role="operador", group_name="EmpresaA")
    op_b = make_user("op-b", role="operador", group_name="EmpresaB")
    lector_b = make_user("lector-b", role="lector", group_name="EmpresaB")

    async with client() as api:
        alta_a = await api.post(
            "/api/devices",
            headers=op_a,
            json={"name": "core-01", "address": "192.0.2.10"},
        )
        commit_a = shared_repo("core-01", _export_de("a", SECRETO_A), -3600)
        await backup_event_repository.record_event("core-01", "node_success", commit_a)
        await backup_integrity_repository.record(
            "core-01", commit_a, 100, 5, 2, ["aviso de A"]
        )
        borrado = await api.delete(f"/api/devices/{alta_a.json()['id']}", headers=op_a)
        assert borrado.status_code == 204
        # El borrado purga lo que sí es purgable: eventos e integridad.
        assert backup_event_repository._events == []
        assert "core-01" not in backup_integrity_repository.veredictos

        alta_b = await api.post(
            "/api/devices",
            headers=op_b,
            json={"name": "core-01", "address": "192.0.2.20"},
        )
        assert alta_b.status_code == 201

        # Sin respaldo propio todavía: nada, ni siquiera el último de A.
        antes = await _nada_del_dueno_anterior(
            api, "core-01", commit_a, lector_b
        )
        assert antes["versions"].json() == []
        assert antes["events"].json() == []

        # Primer respaldo de B: se ve, y su diff no enseña lo que había de A.
        commit_b = shared_repo("core-01", _export_de("b", "ClaveDeB"), 60)
        await backup_event_repository.record_event("core-01", "node_success", commit_b)
        despues = await _nada_del_dueno_anterior(
            api, "core-01", commit_a, lector_b
        )
        assert [v["commit"] for v in despues["versions"].json()] == [commit_b]
        assert [e["commit_ref"] for e in despues["events"].json()] == [commit_b]
        assert despues["status"].json()[0]["last_commit"] == commit_b

        config_b = await api.get(
            "/api/backups/config",
            params={"node": "core-01", "commit": commit_b},
            headers=lector_b,
        )
        diff_b = await api.get(
            "/api/backups/diff",
            params={"node": "core-01", "commit": commit_b},
            headers=lector_b,
        )

    assert config_b.status_code == 200
    assert "ClaveDeB" in config_b.json()["config"]
    assert diff_b.status_code == 200
    assert "router-b" in diff_b.json()["diff"]
    assert "router-a" not in diff_b.json()["diff"]
    assert SECRETO_A not in diff_b.text


async def test_renaming_into_an_old_name_does_not_inherit_its_history(
    auth_headers, make_user, shared_repo, backup_event_repository
) -> None:
    op_a = make_user("op-a", role="operador", group_name="EmpresaA")
    op_b = make_user("op-b", role="operador", group_name="EmpresaB")
    lector_b = make_user("lector-b", role="lector", group_name="EmpresaB")

    async with client() as api:
        alta_a = await api.post(
            "/api/devices", headers=op_a, json={"name": "rb-viejo", "address": "192.0.2.1"}
        )
        alta_b = await api.post(
            "/api/devices", headers=op_b, json={"name": "rb-b", "address": "192.0.2.2"}
        )
        commit_a = shared_repo("rb-viejo", _export_de("a", SECRETO_A), -3600)
        await backup_event_repository.record_event("rb-viejo", "node_success", commit_a)
        _envejecer_eventos(backup_event_repository, "rb-viejo")

        # A se cambia de nombre; lo de `rb-viejo` queda en git y en eventos.
        renombre_a = await api.patch(
            f"/api/devices/{alta_a.json()['id']}", headers=op_a, json={"name": "rb-nuevo"}
        )
        assert renombre_a.status_code == 200
        renombre_b = await api.patch(
            f"/api/devices/{alta_b.json()['id']}", headers=op_b, json={"name": "rb-viejo"}
        )
        assert renombre_b.status_code == 200

        respuestas = await _nada_del_dueno_anterior(
            api, "rb-viejo", commit_a, lector_b
        )

    assert respuestas["versions"].json() == []
    assert respuestas["events"].json() == []
    assert respuestas["status"].json() == []


async def test_moving_a_device_to_another_company_hides_its_past(
    auth_headers, make_user, shared_repo, backup_event_repository
) -> None:
    lector_b = make_user("lector-b", role="lector", group_name="EmpresaB")

    async with client() as api:
        alta = await api.post(
            "/api/devices",
            headers=auth_headers,
            json={"name": "rb-mudanza", "address": "192.0.2.1", "group_name": "EmpresaA"},
        )
        commit_a = shared_repo("rb-mudanza", _export_de("a", SECRETO_A), -3600)
        await backup_event_repository.record_event("rb-mudanza", "node_success", commit_a)
        _envejecer_eventos(backup_event_repository, "rb-mudanza")

        movido = await api.patch(
            f"/api/devices/{alta.json()['id']}",
            headers=auth_headers,
            json={"group_name": "EmpresaB"},
        )
        assert movido.status_code == 200

        respuestas = await _nada_del_dueno_anterior(
            api, "rb-mudanza", commit_a, lector_b
        )

    assert respuestas["versions"].json() == []
    assert respuestas["events"].json() == []
    assert respuestas["status"].json() == []


async def test_editing_other_fields_keeps_the_history(
    auth_headers, make_user, shared_repo, device_repository
) -> None:
    """Cambiar la IP o reenviar la misma empresa no es un cambio de dueño."""
    op_a = make_user("op-a", role="operador", group_name="EmpresaA")

    async with client() as api:
        alta = await api.post(
            "/api/devices", headers=op_a, json={"name": "rb-fijo", "address": "192.0.2.1"}
        )
        commit = shared_repo("rb-fijo", _export_de("a", "x"), 60)
        antes = device_repository.history_since("rb-fijo")
        await api.patch(
            f"/api/devices/{alta.json()['id']}",
            headers=op_a,
            json={"address": "192.0.2.99", "group_name": "EmpresaA"},
        )
        versiones = await api.get(
            "/api/backups/versions", params={"node": "rb-fijo"}, headers=op_a
        )

    assert device_repository.history_since("rb-fijo") == antes
    assert [v["commit"] for v in versiones.json()] == [commit]


async def test_duplicate_name_does_not_reveal_other_companies(
    inventory, make_user
) -> None:
    """El 409 por un nombre de otra empresa es idéntico al de uno propio."""
    op_a = make_user("op-a", role="operador", group_name="EmpresaA")

    async with client() as api:
        propio = await api.post(
            "/api/devices", headers=op_a, json={"name": "rb-alfa", "address": "192.0.2.5"}
        )
        ajeno = await api.post(
            "/api/devices", headers=op_a, json={"name": "rb-beta", "address": "192.0.2.5"}
        )
        renombre = await api.patch(
            f"/api/devices/{inventory['alfa']['id']}",
            headers=op_a,
            json={"name": "rb-beta"},
        )

    assert propio.status_code == ajeno.status_code == renombre.status_code == 409
    assert propio.json() == ajeno.json() == renombre.json()
    assert "empresa" not in ajeno.json()["detail"].lower()


async def test_ssh_check_only_sees_and_probes_own_company(
    inventory, make_user, monkeypatch
) -> None:
    """La validación de acceso previa al respaldo devuelve nombres e IPs: un
    operador de una empresa no debe ver ni hacer conectar a equipos de otra,
    aunque pida sus ids."""
    sondeados = []

    async def fake_validar(address, port, username, password):
        sondeados.append(address)
        return "sin_conexion"

    monkeypatch.setattr("app.backups._validar_acceso_ssh", fake_validar)
    headers = make_user("op-a", role="operador", group_name="EmpresaA")

    async with client() as api:
        todos = await api.post("/api/backups/check", headers=headers, json={"scope": "all"})
        por_ids = await api.post(
            "/api/backups/check",
            headers=headers,
            json={
                "scope": "devices",
                "device_ids": [inventory["alfa"]["id"], inventory["beta"]["id"]],
            },
        )

    for response in (todos, por_ids):
        assert response.status_code == 200
        nombres = [d["name"] for d in response.json()["reachable"] + response.json()["unreachable"]]
        assert nombres == ["rb-alfa"]
    assert inventory["beta"]["address"] not in sondeados
