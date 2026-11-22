"""Limpieza de eventos de respaldo viejos (BackupEventRepository.purge)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import scheduler


pytestmark = pytest.mark.anyio


def _sembrar(repo, node: str, event: str, dias: int, commit: str = "") -> int:
    repo._events.append(
        {
            "id": repo._next_id,
            "node": node,
            "event": event,
            "commit_ref": commit,
            "created_at": datetime.now(timezone.utc) - timedelta(days=dias),
        }
    )
    repo._next_id += 1
    return repo._next_id - 1


async def test_borra_lo_viejo_y_deja_lo_reciente(backup_event_repository) -> None:
    repo = backup_event_repository
    viejo = _sembrar(repo, "rb-lab-01", "node_success", 500)
    _sembrar(repo, "rb-lab-01", "post_store", 500, "aaa111")
    reciente = [
        _sembrar(repo, "rb-lab-01", "node_success", 2),
        _sembrar(repo, "rb-lab-01", "post_store", 2, "bbb222"),
    ]

    borrados = await repo.purge(365)

    assert borrados == 2
    ids = {e["id"] for e in repo._events}
    assert viejo not in ids
    assert ids == set(reciente)


async def test_conserva_el_ultimo_exito_aunque_sea_viejo(
    backup_event_repository,
) -> None:
    """Un equipo que lleva más de un año fallando no pierde la fecha de su
    último respaldo bueno ni el commit para descargarlo."""
    repo = backup_event_repository
    _sembrar(repo, "rb-roto", "node_success", 700)
    _sembrar(repo, "rb-roto", "post_store", 700, "viejo00")
    exito = _sembrar(repo, "rb-roto", "node_success", 600)
    commit = _sembrar(repo, "rb-roto", "post_store", 600, "bueno11")
    _sembrar(repo, "rb-roto", "node_fail", 500)
    ultimo = _sembrar(repo, "rb-roto", "node_fail", 400)

    borrados = await repo.purge(365)

    assert borrados == 3
    assert {e["id"] for e in repo._events} == {exito, commit, ultimo}
    estado = {fila["node"]: fila for fila in await repo.status()}["rb-roto"]
    assert estado["last_event"] == "node_fail"
    assert estado["last_success_at"] is not None
    assert estado["last_commit"] == "bueno11"


async def test_no_mezcla_equipos(backup_event_repository) -> None:
    repo = backup_event_repository
    a = _sembrar(repo, "rb-a", "node_fail", 900)
    b = _sembrar(repo, "rb-b", "node_fail", 800)
    _sembrar(repo, "rb-b", "node_fail", 850)

    assert await repo.purge(365) == 1
    assert {e["id"] for e in repo._events} == {a, b}


async def test_el_programador_usa_la_retencion_configurada(
    backup_event_repository,
) -> None:
    repo = backup_event_repository
    _sembrar(repo, "rb-lab-01", "node_fail", 40)
    _sembrar(repo, "rb-lab-01", "node_fail", 1)
    app = SimpleNamespace(state=SimpleNamespace(backup_events=repo))

    conservados = await scheduler.purge_backup_events(
        app, SimpleNamespace(backup_events_retention_days=365)
    )
    borrados = await scheduler.purge_backup_events(
        app, SimpleNamespace(backup_events_retention_days=30)
    )

    assert conservados == 0
    assert borrados == 1


async def test_un_fallo_de_la_base_no_tumba_el_ciclo() -> None:
    class Roto:
        async def purge(self, days: int) -> int:
            raise RuntimeError("base caída")

    app = SimpleNamespace(state=SimpleNamespace(backup_events=Roto()))

    assert (
        await scheduler.purge_backup_events(
            app, SimpleNamespace(backup_events_retention_days=365)
        )
        == 0
    )
