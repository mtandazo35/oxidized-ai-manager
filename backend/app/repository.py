from typing import Any

import asyncpg


class DuplicateDeviceError(Exception):
    """Raised when a device name already exists in the inventory."""


PUBLIC_COLUMNS = "id, name, address, model, username, enabled, created_at, updated_at"


class DeviceRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

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
                "INSERT INTO devices (name, address, model, username, password, enabled) "
                f"VALUES ($1, $2, $3, $4, $5, $6) RETURNING {PUBLIC_COLUMNS}",
                data["name"],
                data["address"],
                data["model"],
                data["username"],
                data["password"],
                data["enabled"],
            )
        except asyncpg.UniqueViolationError as exc:
            raise DuplicateDeviceError(data["name"]) from exc
        return dict(row)

    async def update_device(
        self, device_id: int, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not data:
            return await self.get_device(device_id)
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

    async def delete_device(self, device_id: int) -> bool:
        result = await self._pool.execute(
            "DELETE FROM devices WHERE id = $1", device_id
        )
        return result == "DELETE 1"

    async def list_oxidized_nodes(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT name, address, model, username, password "
            "FROM devices WHERE enabled ORDER BY name"
        )
        return [dict(row) for row in rows]
