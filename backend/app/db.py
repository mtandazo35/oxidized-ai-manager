import asyncpg

from .config import Settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;

-- Multi-tenant. El DEFAULT 'admin' es deliberado SOLO para la migración: las
-- cuentas que ya existían son administradores. Acto seguido el default pasa a
-- 'lector', que es el rol inofensivo para cualquier alta futura sin rol.
ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'admin';
ALTER TABLE users ALTER COLUMN role SET DEFAULT 'lector';
-- Empresa a la que pertenece la cuenta; vacío = todas (solo para admin).
ALTER TABLE users ADD COLUMN IF NOT EXISTS group_name TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS devices_group_idx ON devices (group_name);

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
ALTER TABLE devices ADD COLUMN IF NOT EXISTS group_name TEXT NOT NULL DEFAULT '';
ALTER TABLE devices ADD COLUMN IF NOT EXISTS backup_interval_minutes INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS backup_events (
    id SERIAL PRIMARY KEY,
    node TEXT NOT NULL,
    event TEXT NOT NULL,
    commit_ref TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS backup_events_node_idx
    ON backup_events (node, created_at DESC);

-- Bitácora: qué pasó, cuándo, quién y desde dónde.
CREATE TABLE IF NOT EXISTS activity_log (
    id BIGSERIAL PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    username TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    ok BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS activity_log_at_idx ON activity_log (at DESC);
CREATE INDEX IF NOT EXISTS activity_log_action_idx ON activity_log (action, at DESC);
CREATE INDEX IF NOT EXISTS activity_log_user_idx ON activity_log (username, at DESC);

-- Bloqueos por IP y por cuenta. En base y no en memoria: los de memoria se
-- borraban en cada reinicio del backend, que es justo lo que provoca un
-- ataque sostenido.
CREATE TABLE IF NOT EXISTS ip_blocks (
    ip TEXT PRIMARY KEY,
    blocked_until TIMESTAMPTZ NOT NULL,
    failures INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS account_locks (
    username TEXT PRIMARY KEY,
    locked_until TIMESTAMPTZ NOT NULL,
    failures INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
