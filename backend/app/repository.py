from typing import Any

import asyncpg

from .config import get_settings
from .secrets_box import decrypt_secret, encrypt_secret, looks_encrypted


class DuplicateDeviceError(Exception):
    """Raised when a device name already exists in the inventory."""


PUBLIC_COLUMNS = (
    "id, name, address, port, model, username, enabled, group_name, "
    "backup_interval_minutes, identity, ros_version, board, created_at, updated_at"
)

METADATA_FIELDS = ("identity", "ros_version", "board")


class DeviceRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @property
    def _key(self) -> str:
        return get_settings().app_secret_key

    async def encrypt_legacy_passwords(self) -> int:
        """Cifra en reposo cualquier clave de router aún en texto plano."""
        rows = await self._pool.fetch(
            "SELECT id, password FROM devices WHERE password <> ''"
        )
        migrated = 0
        for row in rows:
            if looks_encrypted(self._key, row["password"]):
                continue
            await self._pool.execute(
                "UPDATE devices SET password = $2 WHERE id = $1",
                row["id"],
                encrypt_secret(self._key, row["password"]),
            )
            migrated += 1
        return migrated

    async def list_devices(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT {PUBLIC_COLUMNS} FROM devices ORDER BY name"
        )
        return [dict(row) for row in rows]

    async def get_device(self, device_id: int) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            f"SELECT {PUBLIC_COLUMNS} FROM devices WHERE id = $1", device_id
        )
        return dict(row) if row else None

    async def create_device(self, data: dict[str, Any]) -> dict[str, Any]:
        try:
            row = await self._pool.fetchrow(
                "INSERT INTO devices "
                "(name, address, port, model, username, password, enabled, "
                "group_name, backup_interval_minutes) "
                f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
                f"RETURNING {PUBLIC_COLUMNS}",
                data["name"],
                data["address"],
                data["port"],
                data["model"],
                data["username"],
                encrypt_secret(self._key, data["password"]),
                data["enabled"],
                data["group_name"],
                data["backup_interval_minutes"],
            )
        except asyncpg.UniqueViolationError as exc:
            raise DuplicateDeviceError(data["name"]) from exc
        return dict(row)

    async def update_device(
        self, device_id: int, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not data:
            return await self.get_device(device_id)
        if "password" in data:
            data = {**data, "password": encrypt_secret(self._key, data["password"])}
        assignments = []
        values: list[Any] = []
        for position, (column, value) in enumerate(data.items(), start=2):
            assignments.append(f"{column} = ${position}")
            values.append(value)
        query = (
            f"UPDATE devices SET {', '.join(assignments)}, updated_at = now() "
            f"WHERE id = $1 RETURNING {PUBLIC_COLUMNS}"
        )
        try:
            row = await self._pool.fetchrow(query, device_id, *values)
        except asyncpg.UniqueViolationError as exc:
            raise DuplicateDeviceError(data.get("name", "")) from exc
        return dict(row) if row else None

    async def update_metadata(self, name: str, meta: dict[str, str]) -> None:
        values = {key: meta[key] for key in METADATA_FIELDS if key in meta}
        if not values:
            return
        assignments = ", ".join(
            f"{column} = ${position}"
            for position, column in enumerate(values, start=2)
        )
        await self._pool.execute(
            f"UPDATE devices SET {assignments}, updated_at = now() WHERE name = $1",
            name,
            *values.values(),
        )

    async def delete_device(self, device_id: int) -> bool:
        result = await self._pool.execute(
            "DELETE FROM devices WHERE id = $1", device_id
        )
        return result == "DELETE 1"

    async def list_oxidized_nodes(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT name, address, port, model, username, password "
            "FROM devices WHERE enabled ORDER BY name"
        )
        nodes = []
        for row in rows:
            node = dict(row)
            node["password"] = decrypt_secret(self._key, node["password"])
            nodes.append(node)
        return nodes


SETTINGS_DEFAULTS = {
    "backup_interval_minutes": "60",
    "git_remote_enabled": "false",
    "git_remote_url": "",
    "git_push_interval_minutes": "60",
    "last_push_ok": "",
    "last_push_at": "",
    "last_push_detail": "",
}


class SettingsRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_all(self) -> dict[str, str]:
        rows = await self._pool.fetch("SELECT key, value FROM settings")
        stored = {row["key"]: row["value"] for row in rows}
        return {**SETTINGS_DEFAULTS, **stored}

    async def set_many(self, values: dict[str, str]) -> None:
        async with self._pool.acquire() as connection:
            for key, value in values.items():
                await connection.execute(
                    "INSERT INTO settings (key, value) VALUES ($1, $2) "
                    "ON CONFLICT (key) DO UPDATE "
                    "SET value = EXCLUDED.value, updated_at = now()",
                    key,
                    value,
                )


class BackupEventRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_event(self, node: str, event: str, commit_ref: str) -> None:
        await self._pool.execute(
            "INSERT INTO backup_events (node, event, commit_ref) VALUES ($1, $2, $3)",
            node,
            event,
            commit_ref,
        )

    async def status(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT node,
                   (array_agg(event ORDER BY created_at DESC)
                       FILTER (WHERE event <> 'post_store'))[1] AS last_event,
                   max(created_at) FILTER (WHERE event <> 'post_store')
                       AS last_event_at,
                   max(created_at) FILTER (WHERE event = 'node_success')
                       AS last_success_at,
                   (array_agg(commit_ref ORDER BY created_at DESC)
                       FILTER (WHERE commit_ref <> ''))[1] AS last_commit
            FROM backup_events
            GROUP BY node
            ORDER BY node
            """
        )
        return [dict(row) for row in rows]

    async def list_events(
        self, node: str | None, limit: int
    ) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT id, node, event, commit_ref, created_at FROM backup_events "
            "WHERE ($1::text IS NULL OR node = $1) "
            "ORDER BY created_at DESC LIMIT $2",
            node,
            limit,
        )
        return [dict(row) for row in rows]


class UserRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def count_users(self) -> int:
        return await self._pool.fetchval("SELECT count(*) FROM users")

    async def get_by_username(self, username: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT id, username, password_hash, must_change_password "
            "FROM users WHERE username = $1",
            username,
        )
        return dict(row) if row else None

    async def create_user(
        self, username: str, password_hash: str, must_change_password: bool = False
    ) -> None:
        await self._pool.execute(
            "INSERT INTO users (username, password_hash, must_change_password) "
            "VALUES ($1, $2, $3)",
            username,
            password_hash,
            must_change_password,
        )

    async def update_password(self, username: str, password_hash: str) -> None:
        await self._pool.execute(
            "UPDATE users SET password_hash = $2, must_change_password = FALSE, "
            "updated_at = now() WHERE username = $1",
            username,
            password_hash,
        )
