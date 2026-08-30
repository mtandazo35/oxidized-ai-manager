import os

import pytest


os.environ.setdefault("APP_SECRET_KEY", "test-secret-key")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("REDIS_PASSWORD", "test")
os.environ.setdefault("OXIDIZED_URL", "http://localhost:8888")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
