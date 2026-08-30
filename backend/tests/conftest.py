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
from app.repository import SETTINGS_DEFAULTS, DuplicateDeviceError  # noqa: E402
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
            "identity": "",
            "ros_version": "",
            "board": "",
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

    async def update_metadata(self, name: str, meta: dict[str, Any]) -> None:
        for device in self._devices.values():
            if device["name"] == name:
                for key in ("identity", "ros_version", "board"):
                    if key in meta:
                        device[key] = meta[key]

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
                "port": device["port"],
                "model": device["model"],
                "username": device["username"],
                "password": device["password"],
            }
            for device in enabled
        ]


class FakeBackupEventRepository:
    """In-memory stand-in matching BackupEventRepository's public contract."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._next_id = 1

    async def record_event(self, node: str, event: str, commit_ref: str) -> None:
        self._events.append(
            {
                "id": self._next_id,
                "node": node,
                "event": event,
                "commit_ref": commit_ref,
                "created_at": datetime.now(timezone.utc),
            }
        )
        self._next_id += 1

    async def status(self) -> list[dict[str, Any]]:
        nodes: dict[str, list[dict[str, Any]]] = {}
        for event in self._events:
            nodes.setdefault(event["node"], []).append(event)
        result = []
        for node in sorted(nodes):
            events = nodes[node]
            visible = [e for e in events if e["event"] != "post_store"]
            successes = [e for e in events if e["event"] == "node_success"]
            commits = [e for e in events if e["commit_ref"]]
            result.append(
                {
                    "node": node,
                    "last_event": visible[-1]["event"] if visible else None,
                    "last_event_at": visible[-1]["created_at"] if visible else None,
                    "last_success_at": successes[-1]["created_at"] if successes else None,
                    "last_commit": commits[-1]["commit_ref"] if commits else None,
                }
            )
        return result

    async def list_events(
        self, node: str | None, limit: int
    ) -> list[dict[str, Any]]:
        events = [e for e in self._events if node is None or e["node"] == node]
        return list(reversed(events))[:limit]


class FakeSettingsRepository:
    """In-memory stand-in matching SettingsRepository's public contract."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    async def get_all(self) -> dict[str, str]:
        return {**SETTINGS_DEFAULTS, **self._values}

    async def set_many(self, values: dict[str, str]) -> None:
        self._values.update(values)


class FakeUserRepository:
    """In-memory stand-in matching UserRepository's public contract."""

    def __init__(self) -> None:
        self._users: dict[str, dict[str, Any]] = {}

    async def count_users(self) -> int:
        return len(self._users)

    async def get_by_username(self, username: str) -> dict[str, Any] | None:
        user = self._users.get(username)
        return dict(user) if user else None

    async def create_user(
        self, username: str, password_hash: str, must_change_password: bool = False
    ) -> None:
        self._users[username] = {
            "id": len(self._users) + 1,
            "username": username,
            "password_hash": password_hash,
            "must_change_password": must_change_password,
        }

    async def update_password(self, username: str, password_hash: str) -> None:
        self._users[username]["password_hash"] = password_hash
        self._users[username]["must_change_password"] = False


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
def backup_event_repository() -> FakeBackupEventRepository:
    from app import main as main_module

    repository = FakeBackupEventRepository()
    main_module.app.state.backup_events = repository
    return repository


@pytest.fixture(autouse=True)
def settings_repository() -> FakeSettingsRepository:
    from app import main as main_module

    repository = FakeSettingsRepository()
    main_module.app.state.settings = repository
    return repository


@pytest.fixture(autouse=True)
def user_repository() -> FakeUserRepository:
    from app import main as main_module

    repository = FakeUserRepository()
    repository._users["admin"] = {
        "id": 1,
        "username": "admin",
        "password_hash": hash_password(TEST_ADMIN_PASSWORD),
        "must_change_password": False,
    }
    main_module.app.state.users = repository
    return repository


@pytest.fixture
def auth_headers() -> dict[str, str]:
    settings = get_settings()
    token = create_access_token("admin", settings.app_secret_key, 60)
    return {"Authorization": f"Bearer {token}"}
