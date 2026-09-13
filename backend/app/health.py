import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable

import asyncpg
import httpx
from redis.asyncio import Redis

from .config import Settings


async def check_postgres(settings: Settings) -> None:
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        timeout=3,
    )
    try:
        await connection.fetchval("SELECT 1")
    finally:
        await connection.close()


async def check_redis(settings: Settings) -> None:
    client = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password,
        socket_connect_timeout=3,
        socket_timeout=3,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()


async def check_oxidized(settings: Settings) -> None:
    async with httpx.AsyncClient(timeout=3) as client:
        response = await client.get(f"{settings.oxidized_url.rstrip('/')}/nodes.json")
        response.raise_for_status()


async def _probe(check: Callable[[], Awaitable[None]]) -> bool:
    try:
        await check()
    except Exception:
        return False
    return True


async def check_dependencies(settings: Settings) -> dict[str, bool]:
    results = await asyncio.gather(
        _probe(lambda: check_postgres(settings)),
        _probe(lambda: check_redis(settings)),
        _probe(lambda: check_oxidized(settings)),
    )
    return dict(zip(("postgres", "redis", "oxidized"), results, strict=True))


async def backup_freshness(app, settings: Settings) -> dict:
    """Resumen de antigüedad de los respaldos, para monitoreo externo.

    Deliberadamente **sin nombres de equipos**: lo consulta Uptime Kuma sin
    token, así que solo devuelve recuentos. El detalle por equipo está en
    `/api/backups/status`, que sí exige sesión.

    Un equipo cuenta como atrasado cuando su último respaldo exitoso es más
    viejo que su intervalo por `backup_staleness_factor`. Un equipo que nunca
    se respaldó se mide desde su fecha de alta, para no marcar como atrasado
    algo que se acaba de registrar.
    """
    devices = await app.state.devices.list_devices()
    statuses = {row["node"]: row for row in await app.state.backup_events.status()}
    values = await app.state.settings.get_all()
    try:
        global_minutes = max(int(values["backup_interval_minutes"]), 5)
    except (KeyError, ValueError):
        global_minutes = 60

    now = dt.datetime.now(dt.timezone.utc)
    enabled = [device for device in devices if device["enabled"]]
    stale = 0
    never = 0
    for device in enabled:
        interval = device.get("backup_interval_minutes") or global_minutes
        deadline = dt.timedelta(
            minutes=max(interval, 5) * settings.backup_staleness_factor
        )
        last_success = (statuses.get(device["name"]) or {}).get("last_success_at")
        if last_success is None:
            never += 1
            reference = device.get("created_at") or now
        else:
            reference = last_success
        if now - reference > deadline:
            stale += 1
    return {
        "status": "ok" if stale == 0 else "stale",
        "devices": len(enabled),
        "stale": stale,
        "never_backed_up": never,
        "interval_minutes": global_minutes,
        "staleness_factor": settings.backup_staleness_factor,
    }
