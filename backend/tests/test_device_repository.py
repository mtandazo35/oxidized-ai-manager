"""El `DeviceRepository` real, contra un pool de asyncpg simulado.

El resto de la batería usa `FakeDeviceRepository`, que no ejecuta el SQL ni
cifra las claves. Aquí se comprueba el SQL que importa para el aislamiento
entre empresas y el cifrado de las claves de los routers.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.repository import DeviceRepository
from app.secrets_box import looks_encrypted


pytestmark = pytest.mark.anyio

AHORA = datetime(2026, 10, 27, tzinfo=timezone.utc)


class FakePool:
    """Guarda cada consulta y devuelve lo que la prueba prepare."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_result: dict[str, Any] | None = None
        self.fetch_result: list[dict[str, Any]] = []
        self.fetchval_result: Any = None

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.queries.append((query, args))
        return self.fetchrow_result

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.queries.append((query, args))
        return self.fetch_result

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.queries.append((query, args))
        return self.fetchval_result

    async def execute(self, query: str, *args: Any) -> str:
        self.queries.append((query, args))
        return "OK"

    @asynccontextmanager
    async def transaction(self):
        yield

    @asynccontextmanager
    async def acquire(self):
        yield self


def _fila(**extra: Any) -> dict[str, Any]:
    return {
        "id": 1,
        "name": "rb-1",
        "address": "192.0.2.1",
        "port": 22,
        "model": "routeros",
        "username": "u",
        "enabled": True,
        "group_name": "EmpresaA",
        "backup_interval_minutes": 0,
        "identity": "",
        "ros_version": "",
        "board": "",
        "history_since": AHORA,
        "created_at": AHORA,
        "updated_at": AHORA,
        **extra,
    }


async def test_update_filters_by_company_in_the_update_itself() -> None:
    """Sin el filtro en el WHERE, el equipo podía cambiar de empresa entre la
    comprobación previa y el UPDATE (condición de carrera)."""
    pool = FakePool()
    pool.fetchrow_result = None  # otra empresa: el UPDATE no toca nada
    repository = DeviceRepository(pool)

    result = await repository.update_device(1, {"address": "10.0.0.1"}, "EmpresaA")

    assert result is None
    assert len(pool.queries) == 1, "una sola sentencia, sin consulta previa"
    query, args = pool.queries[0]
    assert query.startswith("UPDATE devices")
    assert "WHERE id = $1 AND group_name = $3" in query
    assert args == (1, "10.0.0.1", "EmpresaA")


async def test_admin_update_has_no_company_filter() -> None:
    pool = FakePool()
    pool.fetchrow_result = _fila()
    repository = DeviceRepository(pool)

    await repository.update_device(1, {"address": "10.0.0.1"}, None)

    query, args = pool.queries[0]
    assert "group_name = $" not in query.split("WHERE")[1]
    assert args == (1, "10.0.0.1")


@pytest.mark.parametrize("campo", ["name", "group_name"])
async def test_renaming_or_moving_restarts_the_visible_history(campo) -> None:
    pool = FakePool()
    pool.fetchrow_result = _fila()
    repository = DeviceRepository(pool)

    await repository.update_device(1, {campo: "nuevo"}, None)

    query, _ = pool.queries[0]
    assert f"history_since = CASE WHEN {campo} IS DISTINCT FROM $2" in query


async def test_other_edits_keep_the_visible_history() -> None:
    pool = FakePool()
    pool.fetchrow_result = _fila()
    repository = DeviceRepository(pool)

    await repository.update_device(1, {"address": "10.0.0.1", "port": 2222}, None)

    assert "history_since" not in pool.queries[0][0].split("RETURNING")[0]


async def test_creation_starts_the_visible_history_now() -> None:
    pool = FakePool()
    pool.fetchrow_result = _fila()
    repository = DeviceRepository(pool)

    await repository.create_device(
        {
            "name": "rb-1",
            "address": "192.0.2.1",
            "port": 22,
            "model": "routeros",
            "username": "u",
            "password": "clave",
            "enabled": True,
            "group_name": "EmpresaA",
            "backup_interval_minutes": 0,
        }
    )

    query, _ = pool.queries[0]
    assert "history_since" in query
    assert "now())" in query


async def test_delete_purges_events_and_integrity_of_the_node() -> None:
    pool = FakePool()
    pool.fetchval_result = "rb-1"
    repository = DeviceRepository(pool)

    assert await repository.delete_device(1, "EmpresaA") is True

    sentencias = [query for query, _ in pool.queries]
    assert "group_name = $2" in sentencias[0]
    assert any("DELETE FROM backup_events WHERE node = $1" in q for q in sentencias)
    assert any("DELETE FROM backup_integrity WHERE node = $1" in q for q in sentencias)
    assert all(args[0] == "rb-1" for _, args in pool.queries[1:])


async def test_delete_of_another_company_purges_nothing() -> None:
    pool = FakePool()
    pool.fetchval_result = None
    repository = DeviceRepository(pool)

    assert await repository.delete_device(1, "EmpresaA") is False
    assert len(pool.queries) == 1


async def test_history_queries_hide_the_previous_owner() -> None:
    """Eventos e integridad se cortan en `history_since` del dueño actual."""
    from app.repository import BackupEventRepository

    pool = FakePool()
    repository = BackupEventRepository(pool)

    await repository.status(None)
    await repository.list_events("rb-1", 10, None)

    status_sql, events_sql = (query for query, _ in pool.queries)
    assert "e.created_at >= d.history_since" in status_sql
    assert "i.checked_at >= d.history_since" in status_sql
    assert "e.created_at >= d.history_since" in events_sql


async def test_oxidized_nodes_receive_the_decrypted_password(
    monkeypatch,
) -> None:
    """Las claves se guardan cifradas y a Oxidized le llegan descifradas.

    Sustituye a la prueba de `test_security_hardening.py`, que era tautológica:
    su doble guarda la clave tal cual, así que nunca descifraba nada.
    """
    pool = FakePool()
    pool.fetchrow_result = _fila()
    repository = DeviceRepository(pool)
    await repository.create_device(
        {
            "name": "rb-1",
            "address": "192.0.2.1",
            "port": 22,
            "model": "routeros",
            "username": "u",
            "password": "ClaveDelRouter",
            "enabled": True,
            "group_name": "EmpresaA",
            "backup_interval_minutes": 0,
        }
    )
    guardada = pool.queries[0][1][5]
    assert guardada != "ClaveDelRouter"
    assert looks_encrypted(repository._key, guardada)

    pool.fetch_result = [
        {
            "name": "rb-1",
            "address": "192.0.2.1",
            "port": 22,
            "model": "routeros",
            "username": "u",
            "password": guardada,
        }
    ]
    monkeypatch.setattr(main_module.app.state, "devices", repository)
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.get(
            "/api/oxidized/nodes",
            headers={"X-Oxidized-Token": "test-oxidized-token"},
        )

    assert response.status_code == 200
    assert response.json()[0]["password"] == "ClaveDelRouter"
