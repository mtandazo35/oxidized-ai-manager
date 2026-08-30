import os
from datetime import datetime, timezone
from typing import Any

import pytest


os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-0123456789abcdef0123456789abcdef")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("REDIS_PASSWORD", "test")
os.environ.setdefault("OXIDIZED_URL", "http://localhost:8888")
os.environ.setdefault("OXIDIZED_SOURCE_TOKEN", "test-oxidized-token")

from app.config import get_settings  # noqa: E402
from app.repository import DuplicateDeviceError  # noqa: E402
from app.security import create_access_token, hash_password  # noqa: E402


TEST_ADMIN_PASSWORD = "admin-test-password"


class FakeDeviceRepository:
    """In-memory stand-in matching DeviceRepository's public contract."""

    def __init__(self) -> None:
        self._devices: dict[int, dict[str, Any]] = {}
        self._next_id = 1

    @staticmethod
    def _public(device: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in device.items() if key != "password"}

    async def list_devices(self) -> list[dict[str, Any]]:
        devices = sorted(self._devices.values(), key=lambda device: device["name"])
        return [self._public(device) for device in devices]

    async def get_device(self, device_id: int) -> dict[str, Any] | None:
        device = self._devices.get(device_id)
        return self._public(device) if device else None

    async def create_device(self, data: dict[str, Any]) -> dict[str, Any]:
        if any(device["name"] == data["name"] for device in self._devices.values()):
            raise DuplicateDeviceError(data["name"])
        now = datetime.now(timezone.utc)
        device = {
            "id": self._next_id,
            **data,
            "created_at": now,
            "updated_at": now,
        }
        self._devices[self._next_id] = device
        self._next_id += 1
        return self._public(device)

    async def update_device(
        self, device_id: int, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        device = self._devices.get(device_id)
        if device is None:
            return None
        new_name = data.get("name")
        if new_name and any(
            other["name"] == new_name and other_id != device_id
            for other_id, other in self._devices.items()
        ):
            raise DuplicateDeviceError(new_name)
        device.update(data)
        device["updated_at"] = datetime.now(timezone.utc)
        return self._public(device)

    async def delete_device(self, device_id: int) -> bool:
        return self._devices.pop(device_id, None) is not None

    async def list_oxidized_nodes(self) -> list[dict[str, Any]]:
        enabled = sorted(
            (device for device in self._devices.values() if device["enabled"]),
            key=lambda device: device["name"],
        )
        return [
            {
                "name": device["name"],
                "address": device["address"],
                "model": device["model"],
                "username": device["username"],
                "password": device["password"],
            }
            for device in enabled
        ]


class FakeUserRepository:
    """In-memory stand-in matching UserRepository's public contract."""

    def __init__(self) -> None:
        self._users: dict[str, dict[str, Any]] = {}

    async def count_users(self) -> int:
        return len(self._users)

    async def get_by_username(self, username: str) -> dict[str, Any] | None:
        user = self._users.get(username)
        return dict(user) if user else None

    async def create_user(self, username: str, password_hash: str) -> None:
        self._users[username] = {
            "id": len(self._users) + 1,
            "username": username,
            "password_hash": password_hash,
        }

    async def update_password(self, username: str, password_hash: str) -> None:
        self._users[username]["password_hash"] = password_hash


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def device_repository() -> FakeDeviceRepository:
    from app import main as main_module

    repository = FakeDeviceRepository()
    main_module.app.state.devices = repository
    return repository


@pytest.fixture(autouse=True)
def user_repository() -> FakeUserRepository:
    from app import main as main_module

    repository = FakeUserRepository()
    repository._users["admin"] = {
        "id": 1,
        "username": "admin",
        "password_hash": hash_password(TEST_ADMIN_PASSWORD),
    }
    main_module.app.state.users = repository
    return repository


@pytest.fixture
def auth_headers() -> dict[str, str]:
    settings = get_settings()
    token = create_access_token("admin", settings.app_secret_key, 60)
    return {"Authorization": f"Bearer {token}"}
