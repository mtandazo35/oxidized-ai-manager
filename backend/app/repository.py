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

    async def list_devices(self, group: str | None = None) -> list[dict[str, Any]]:
        """Equipos visibles. `group=None` es "todas las empresas" (admin)."""
        if group is None:
            rows = await self._pool.fetch(
                f"SELECT {PUBLIC_COLUMNS} FROM devices ORDER BY name"
            )
        else:
            rows = await self._pool.fetch(
                f"SELECT {PUBLIC_COLUMNS} FROM devices WHERE group_name = $1 "
                "ORDER BY name",
                group,
            )
        return [dict(row) for row in rows]

    async def get_device(
        self, device_id: int, group: str | None = None
    ) -> dict[str, Any] | None:
        if group is None:
            row = await self._pool.fetchrow(
                f"SELECT {PUBLIC_COLUMNS} FROM devices WHERE id = $1", device_id
            )
        else:
            row = await self._pool.fetchrow(
                f"SELECT {PUBLIC_COLUMNS} FROM devices "
                "WHERE id = $1 AND group_name = $2",
                device_id,
                group,
            )
        return dict(row) if row else None

    async def get_device_by_name(
        self, name: str, group: str | None = None
    ) -> dict[str, Any] | None:
        """Usado para autorizar endpoints que reciben el nombre del nodo."""
        if group is None:
            row = await self._pool.fetchrow(
                f"SELECT {PUBLIC_COLUMNS} FROM devices WHERE name = $1", name
            )
        else:
            row = await self._pool.fetchrow(
                f"SELECT {PUBLIC_COLUMNS} FROM devices "
                "WHERE name = $1 AND group_name = $2",
                name,
                group,
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
        self, device_id: int, data: dict[str, Any], group: str | None = None
    ) -> dict[str, Any] | None:
        if group is not None and await self.get_device(device_id, group) is None:
            return None
        if not data:
            return await self.get_device(device_id, group)
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

    async def delete_device(self, device_id: int, group: str | None = None) -> bool:
        if group is None:
            result = await self._pool.execute(
                "DELETE FROM devices WHERE id = $1", device_id
            )
        else:
            result = await self._pool.execute(
                "DELETE FROM devices WHERE id = $1 AND group_name = $2",
                device_id,
                group,
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


# Ajustes que se guardan cifrados: la URL del Git remoto puede llevar un token
# embebido (`https://usuario:token@host/repo.git`) y un volcado de la base no
# debe entregarlo en claro. Los consumidores siguen viendo el valor descifrado.
ENCRYPTED_SETTINGS = frozenset({"git_remote_url"})


class SettingsRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @property
    def _key(self) -> str:
        return get_settings().app_secret_key

    async def encrypt_legacy_settings(self) -> int:
        """Cifra los ajustes sensibles que quedaron en texto plano."""
        migrated = 0
        for key in ENCRYPTED_SETTINGS:
            value = await self._pool.fetchval(
                "SELECT value FROM settings WHERE key = $1", key
            )
            if not value or looks_encrypted(self._key, value):
                continue
            await self._pool.execute(
                "UPDATE settings SET value = $2, updated_at = now() WHERE key = $1",
                key,
                encrypt_secret(self._key, value),
            )
            migrated += 1
        return migrated

    async def get_all(self) -> dict[str, str]:
        rows = await self._pool.fetch("SELECT key, value FROM settings")
        stored = {
            row["key"]: (
                decrypt_secret(self._key, row["value"])
                if row["key"] in ENCRYPTED_SETTINGS
                else row["value"]
            )
            for row in rows
        }
        return {**SETTINGS_DEFAULTS, **stored}

    async def set_many(self, values: dict[str, str]) -> None:
        async with self._pool.acquire() as connection:
            for key, value in values.items():
                if key in ENCRYPTED_SETTINGS:
                    value = encrypt_secret(self._key, value)
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

    async def status(self, nodes: list[str] | None = None) -> list[dict[str, Any]]:
        """`nodes=None` es "todos"; una lista vacía no devuelve nada."""
        if nodes is not None and not nodes:
            return []
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
            WHERE ($1::text[] IS NULL OR node = ANY($1))
            GROUP BY node
            ORDER BY node
            """,
            nodes,
        )
        return [dict(row) for row in rows]

    async def list_events(
        self, node: str | None, limit: int, nodes: list[str] | None = None
    ) -> list[dict[str, Any]]:
        if nodes is not None and not nodes:
            return []
        rows = await self._pool.fetch(
            "SELECT id, node, event, commit_ref, created_at FROM backup_events "
            "WHERE ($1::text IS NULL OR node = $1) "
            "AND ($3::text[] IS NULL OR node = ANY($3)) "
            "ORDER BY created_at DESC LIMIT $2",
            node,
            limit,
            nodes,
        )
        return [dict(row) for row in rows]


class DuplicateUserError(Exception):
    """Raised when a username already exists."""


USER_PUBLIC_COLUMNS = (
    "id, username, role, group_name, must_change_password, created_at"
)


class UserRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def count_users(self) -> int:
        return await self._pool.fetchval("SELECT count(*) FROM users")

    async def count_admins(self) -> int:
        return await self._pool.fetchval(
            "SELECT count(*) FROM users WHERE role = 'admin'"
        )

    async def get_by_username(self, username: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT id, username, password_hash, must_change_password, "
            "role, group_name FROM users WHERE username = $1",
            username,
        )
        return dict(row) if row else None

    async def list_users(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            f"SELECT {USER_PUBLIC_COLUMNS} FROM users ORDER BY username"
        )
        return [dict(row) for row in rows]

    async def create_user(
        self,
        username: str,
        password_hash: str,
        must_change_password: bool = False,
        role: str = "lector",
        group_name: str = "",
    ) -> dict[str, Any]:
        try:
            row = await self._pool.fetchrow(
                "INSERT INTO users "
                "(username, password_hash, must_change_password, role, group_name) "
                f"VALUES ($1, $2, $3, $4, $5) RETURNING {USER_PUBLIC_COLUMNS}",
                username,
                password_hash,
                must_change_password,
                role,
                group_name,
            )
        except asyncpg.UniqueViolationError as exc:
            raise DuplicateUserError(username) from exc
        return dict(row)

    async def update_user(
        self, username: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not data:
            return await self.get_public(username)
        assignments = []
        values: list[Any] = []
        for position, (column, value) in enumerate(data.items(), start=2):
            assignments.append(f"{column} = ${position}")
            values.append(value)
        row = await self._pool.fetchrow(
            f"UPDATE users SET {', '.join(assignments)}, updated_at = now() "
            f"WHERE username = $1 RETURNING {USER_PUBLIC_COLUMNS}",
            username,
            *values,
        )
        return dict(row) if row else None

    async def get_public(self, username: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            f"SELECT {USER_PUBLIC_COLUMNS} FROM users WHERE username = $1", username
        )
        return dict(row) if row else None

    async def delete_user(self, username: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM users WHERE username = $1", username
        )
        return result == "DELETE 1"

    async def update_password(self, username: str, password_hash: str) -> None:
        await self._pool.execute(
            "UPDATE users SET password_hash = $2, must_change_password = FALSE, "
            "updated_at = now() WHERE username = $1",
            username,
            password_hash,
        )
