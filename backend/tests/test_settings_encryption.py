"""La URL del Git remoto puede llevar un token embebido: no debe quedar en
texto plano en PostgreSQL. Se prueba contra un doble del pool de asyncpg,
porque lo que importa es qué se escribe en la tabla."""

from contextlib import asynccontextmanager

import pytest

from app.config import get_settings
from app.repository import SettingsRepository
from app.secrets_box import looks_encrypted


pytestmark = pytest.mark.anyio

REMOTE_WITH_TOKEN = "https://mtandazo:ghp_tokensecreto123@github.com/u/respaldos.git"


class FakePool:
    """Mínimo necesario de la interfaz de `asyncpg.Pool` para la tabla settings."""

    def __init__(self, rows: dict[str, str] | None = None) -> None:
        self.rows: dict[str, str] = dict(rows or {})

    async def fetch(self, query: str, *args):
        return [{"key": key, "value": value} for key, value in self.rows.items()]

    async def fetchval(self, query: str, *args):
        return self.rows.get(args[0]) if args else None

    async def execute(self, query: str, *args):
        if query.startswith("UPDATE settings"):
            key, value = args[0], args[1]
        else:  # INSERT ... ON CONFLICT
            key, value = args[0], args[1]
        self.rows[key] = value
        return "OK"

    @asynccontextmanager
    async def acquire(self):
        yield self


@pytest.fixture
def key() -> str:
    return get_settings().app_secret_key


async def test_git_remote_url_is_stored_encrypted(key) -> None:
    pool = FakePool()
    repository = SettingsRepository(pool)

    await repository.set_many({"git_remote_url": REMOTE_WITH_TOKEN})

    stored = pool.rows["git_remote_url"]
    assert stored != REMOTE_WITH_TOKEN
    assert "ghp_tokensecreto123" not in stored
    assert looks_encrypted(key, stored)


async def test_callers_still_read_the_plaintext_url() -> None:
    pool = FakePool()
    repository = SettingsRepository(pool)

    await repository.set_many({"git_remote_url": REMOTE_WITH_TOKEN})
    values = await repository.get_all()

    assert values["git_remote_url"] == REMOTE_WITH_TOKEN


async def test_non_sensitive_settings_stay_readable_in_the_table() -> None:
    pool = FakePool()
    repository = SettingsRepository(pool)

    await repository.set_many({"backup_interval_minutes": "120"})

    assert pool.rows["backup_interval_minutes"] == "120"


async def test_legacy_plaintext_url_is_migrated_on_startup(key) -> None:
    pool = FakePool({"git_remote_url": REMOTE_WITH_TOKEN})
    repository = SettingsRepository(pool)

    migrated = await repository.encrypt_legacy_settings()

    assert migrated == 1
    assert looks_encrypted(key, pool.rows["git_remote_url"])
    assert (await repository.get_all())["git_remote_url"] == REMOTE_WITH_TOKEN


async def test_migration_is_idempotent() -> None:
    pool = FakePool({"git_remote_url": REMOTE_WITH_TOKEN})
    repository = SettingsRepository(pool)

    await repository.encrypt_legacy_settings()
    ciphertext = pool.rows["git_remote_url"]

    assert await repository.encrypt_legacy_settings() == 0
    assert pool.rows["git_remote_url"] == ciphertext


async def test_empty_url_needs_no_migration() -> None:
    pool = FakePool({"git_remote_url": ""})

    assert await SettingsRepository(pool).encrypt_legacy_settings() == 0
