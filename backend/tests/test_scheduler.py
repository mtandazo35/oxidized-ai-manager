import asyncio

import pytest

from app.scheduler import is_due


def test_is_due_respects_interval() -> None:
    assert is_due(60 * 60, 60) is True
    assert is_due(59 * 60, 60) is False


def test_is_due_enforces_minimum_of_five_minutes() -> None:
    assert is_due(4 * 60, 1) is False
    assert is_due(5 * 60, 0) is True


@pytest.mark.anyio
async def test_push_timeout_reaps_the_process(monkeypatch) -> None:
    """Regresión: tras `kill()` no se esperaba al proceso y quedaba un zombi."""
    from app import scheduler

    class Proceso:
        returncode = None
        esperado = False

        async def communicate(self):
            await asyncio.sleep(10)

        def kill(self) -> None:
            pass

        async def wait(self) -> int:
            Proceso.esperado = True
            return -9

    async def crear(*args, **kwargs):
        return Proceso()

    async def wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(scheduler.asyncio, "create_subprocess_exec", crear)
    monkeypatch.setattr(scheduler.asyncio, "wait_for", wait_for)
    ok, detalle = await scheduler.push_backups("/repo", "https://h/r.git")

    assert ok is False
    assert "timeout" in detalle
    assert Proceso.esperado is True
