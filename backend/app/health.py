import asyncio
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
