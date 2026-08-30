import asyncpg

from .config import Settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS devices (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    address TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 22,
    model TEXT NOT NULL DEFAULT 'routeros',
    username TEXT NOT NULL DEFAULT '',
    password TEXT NOT NULL DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE devices ADD COLUMN IF NOT EXISTS port INTEGER NOT NULL DEFAULT 22;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS identity TEXT NOT NULL DEFAULT '';
ALTER TABLE devices ADD COLUMN IF NOT EXISTS ros_version TEXT NOT NULL DEFAULT '';
ALTER TABLE devices ADD COLUMN IF NOT EXISTS board TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS backup_events (
    id SERIAL PRIMARY KEY,
    node TEXT NOT NULL,
    event TEXT NOT NULL,
    commit_ref TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS backup_events_node_idx
    ON backup_events (node, created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def create_pool(settings: Settings) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        min_size=1,
        max_size=5,
        timeout=10,
    )


async def init_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as connection:
        await connection.execute(SCHEMA)
