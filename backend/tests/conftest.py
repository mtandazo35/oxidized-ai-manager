import os
from datetime import datetime, timedelta, timezone
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
from app.repository import (  # noqa: E402
    SETTINGS_DEFAULTS,
    DuplicateDeviceError,
    DuplicateUserError,
)
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

    @staticmethod
    def _in_group(device: dict[str, Any], group: str | None) -> bool:
        return group is None or device.get("group_name", "") == group

    async def list_devices(self, group: str | None = None) -> list[dict[str, Any]]:
        devices = sorted(self._devices.values(), key=lambda device: device["name"])
        return [
            self._public(device)
            for device in devices
            if self._in_group(device, group)
        ]

    async def get_device(
        self, device_id: int, group: str | None = None
    ) -> dict[str, Any] | None:
        device = self._devices.get(device_id)
        if device is None or not self._in_group(device, group):
            return None
        return self._public(device)

    async def get_device_by_name(
        self, name: str, group: str | None = None
    ) -> dict[str, Any] | None:
        for device in self._devices.values():
            if device["name"] == name and self._in_group(device, group):
                return self._public(device)
        return None

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
        self, device_id: int, data: dict[str, Any], group: str | None = None
    ) -> dict[str, Any] | None:
        device = self._devices.get(device_id)
        if device is None or not self._in_group(device, group):
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

    async def delete_device(self, device_id: int, group: str | None = None) -> bool:
        device = self._devices.get(device_id)
        if device is None or not self._in_group(device, group):
            return False
        del self._devices[device_id]
        return True

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

    async def status(self, nodes_filter: list[str] | None = None) -> list[dict[str, Any]]:
        if nodes_filter is not None and not nodes_filter:
            return []
        nodes: dict[str, list[dict[str, Any]]] = {}
        for event in self._events:
            if nodes_filter is not None and event["node"] not in nodes_filter:
                continue
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
        self, node: str | None, limit: int, nodes: list[str] | None = None
    ) -> list[dict[str, Any]]:
        if nodes is not None and not nodes:
            return []
        events = [
            e
            for e in self._events
            if (node is None or e["node"] == node)
            and (nodes is None or e["node"] in nodes)
        ]
        return list(reversed(events))[:limit]


class FakeSettingsRepository:
    """In-memory stand-in matching SettingsRepository's public contract."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    async def get_all(self) -> dict[str, str]:
        return {**SETTINGS_DEFAULTS, **self._values}

    async def set_many(self, values: dict[str, str]) -> None:
        self._values.update(values)

    async def encrypt_legacy_settings(self) -> int:
        return 0


class FakeUserRepository:
    """In-memory stand-in matching UserRepository's public contract."""

    PUBLIC = ("id", "username", "role", "group_name", "must_change_password")

    def __init__(self) -> None:
        self._users: dict[str, dict[str, Any]] = {}

    @classmethod
    def _public(cls, user: dict[str, Any]) -> dict[str, Any]:
        return {key: user.get(key) for key in cls.PUBLIC}

    async def count_users(self) -> int:
        return len(self._users)

    async def count_admins(self) -> int:
        return sum(1 for user in self._users.values() if user.get("role") == "admin")

    async def get_by_username(self, username: str) -> dict[str, Any] | None:
        user = self._users.get(username)
        return dict(user) if user else None

    async def get_public(self, username: str) -> dict[str, Any] | None:
        user = self._users.get(username)
        return self._public(user) if user else None

    async def list_users(self) -> list[dict[str, Any]]:
        return [self._public(self._users[name]) for name in sorted(self._users)]

    async def create_user(
        self,
        username: str,
        password_hash: str,
        must_change_password: bool = False,
        role: str = "lector",
        group_name: str = "",
    ) -> dict[str, Any]:
        if username in self._users:
            raise DuplicateUserError(username)
        self._users[username] = {
            "id": len(self._users) + 1,
            "username": username,
            "password_hash": password_hash,
            "must_change_password": must_change_password,
            "role": role,
            "group_name": group_name,
        }
        return self._public(self._users[username])

    async def update_user(
        self, username: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        user = self._users.get(username)
        if user is None:
            return None
        user.update(data)
        return self._public(user)

    async def delete_user(self, username: str) -> bool:
        return self._users.pop(username, None) is not None

    async def update_password(self, username: str, password_hash: str) -> None:
        self._users[username]["password_hash"] = password_hash
        self._users[username]["must_change_password"] = False


class FakeActivityRepository:
    """In-memory stand-in matching ActivityRepository's public contract."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self._next_id = 1

    async def record(
        self,
        action: str,
        username: str = "",
        ip: str = "",
        target: str = "",
        detail: str = "",
        ok: bool = True,
    ) -> None:
        self.entries.append(
            {
                "id": self._next_id,
                "at": datetime.now(timezone.utc),
                "action": action,
                "username": username,
                "ip": ip,
                "target": target,
                "detail": detail,
                "ok": ok,
            }
        )
        self._next_id += 1

    async def list_entries(
        self,
        limit: int = 100,
        action: str | None = None,
        username: str | None = None,
        ip: str | None = None,
        only_failures: bool = False,
    ) -> list[dict[str, Any]]:
        rows = [
            entry
            for entry in reversed(self.entries)
            if (action is None or entry["action"].startswith(action))
            and (username is None or entry["username"] == username)
            and (ip is None or entry["ip"] == ip)
            and (not only_failures or not entry["ok"])
        ]
        return rows[:limit]

    async def recent_logins(self, limit: int = 20) -> list[dict[str, Any]]:
        vistos = {}
        for entry in self.entries:
            if entry["action"] == "login.ok":
                vistos[(entry["username"], entry["ip"])] = entry
        return list(vistos.values())[:limit]

    async def purge(self, days: int) -> int:
        return 0

    def actions(self) -> list[str]:
        """Atajo para las pruebas."""
        return [entry["action"] for entry in self.entries]


class FakeAccessRepository:
    """In-memory stand-in matching AccessRepository's public contract."""

    def __init__(self) -> None:
        self.ips: dict[str, dict[str, Any]] = {}
        self.accounts: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _vigente(registro: dict[str, Any] | None, campo: str) -> bool:
        return bool(registro) and registro[campo] > datetime.now(timezone.utc)

    async def ip_block(self, ip: str) -> dict[str, Any] | None:
        registro = self.ips.get(ip)
        return registro if self._vigente(registro, "blocked_until") else None

    async def list_ip_blocks(self) -> list[dict[str, Any]]:
        return [r for r in self.ips.values() if self._vigente(r, "blocked_until")]

    async def register_ip_failure(
        self, ip: str, window_minutes: int, threshold: int, block_minutes: int
    ) -> bool:
        registro = self.ips.setdefault(
            ip,
            {
                "ip": ip,
                "failures": 0,
                "blocked_until": datetime.now(timezone.utc),
                "reason": "",
            },
        )
        registro["failures"] += 1
        if registro["failures"] < threshold:
            return False
        registro["blocked_until"] = datetime.now(timezone.utc) + timedelta(
            minutes=block_minutes
        )
        registro["reason"] = f"{registro['failures']} intentos fallidos"
        return True

    async def clear_ip(self, ip: str) -> bool:
        return self.ips.pop(ip, None) is not None

    async def account_lock(self, username: str) -> dict[str, Any] | None:
        registro = self.accounts.get(username)
        return registro if self._vigente(registro, "locked_until") else None

    async def list_account_locks(self) -> list[dict[str, Any]]:
        return [
            r for r in self.accounts.values() if self._vigente(r, "locked_until")
        ]

    async def register_account_failure(
        self, username: str, window_minutes: int, threshold: int, lock_minutes: int
    ) -> bool:
        registro = self.accounts.setdefault(
            username,
            {
                "username": username,
                "failures": 0,
                "locked_until": datetime.now(timezone.utc),
            },
        )
        registro["failures"] += 1
        if registro["failures"] < threshold:
            return False
        registro["locked_until"] = datetime.now(timezone.utc) + timedelta(
            minutes=lock_minutes
        )
        return True

    async def clear_account(self, username: str) -> bool:
        return self.accounts.pop(username, None) is not None


@pytest.fixture(autouse=True)
def activity_repository() -> FakeActivityRepository:
    from app import main as main_module

    repository = FakeActivityRepository()
    main_module.app.state.activity = repository
    return repository


@pytest.fixture(autouse=True)
def access_repository() -> FakeAccessRepository:
    from app import main as main_module

    repository = FakeAccessRepository()
    main_module.app.state.access = repository
    return repository


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def no_network_backups(monkeypatch):
    """Evita que el primer respaldo salga a la red durante las pruebas.

    Al dar de alta un equipo, la API encola su primer respaldo en segundo
    plano. Sin este doble, cada alta intentaba contactar con Oxidized de
    verdad y se comía el tiempo de espera: la batería pasó de 55 s a 226 s.
    Devuelve la lista de nodos encolados para poder comprobarlo.
    """
    encolados: list[str] = []

    async def fake_backup_new_nodes(oxidized_url: str, names: list[str]) -> None:
        encolados.extend(names)

    monkeypatch.setattr("app.devices.backup_new_nodes", fake_backup_new_nodes)
    return encolados


@pytest.fixture(autouse=True)
def reset_login_throttles():
    """Limpia el freno de peticiones por IP entre pruebas.

    Los bloqueos por cuenta y por IP viven ahora en la base (su doble se crea
    nuevo en cada prueba), así que solo queda por reiniciar el contador en
    memoria del middleware.
    """
    from app.middleware import reset_login_rate_limit

    reset_login_rate_limit()
    yield
    reset_login_rate_limit()


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
        "role": "admin",
        "group_name": "",
    }
    main_module.app.state.users = repository
    return repository


@pytest.fixture
def auth_headers() -> dict[str, str]:
    settings = get_settings()
    token = create_access_token("admin", settings.app_secret_key, 60)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def make_user(user_repository):
    """Crea una cuenta y devuelve su cabecera de autorización."""

    def factory(
        username: str, role: str = "lector", group_name: str = "", password: str = "x"
    ) -> dict[str, str]:
        user_repository._users[username] = {
            "id": len(user_repository._users) + 1,
            "username": username,
            "password_hash": hash_password(password),
            "must_change_password": False,
            "role": role,
            "group_name": group_name,
        }
        token = create_access_token(
            username, get_settings().app_secret_key, 60
        )
        return {"Authorization": f"Bearer {token}"}

    return factory
