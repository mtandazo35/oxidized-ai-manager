"""Configuración: no se arranca con los secretos de `.env.example`."""

import pytest
from pydantic import ValidationError

from app.config import Settings


BASE = {
    "postgres_host": "db",
    "postgres_db": "x",
    "postgres_user": "x",
    "postgres_password": "x",
    "redis_host": "redis",
    "redis_password": "x",
    "oxidized_url": "http://oxidized:8888",
    "oxidized_source_token": "a" * 64,
    "app_secret_key": "b" * 64,
}


def build(**overrides) -> Settings:
    return Settings(_env_file=None, **{**BASE, **overrides})


def test_valid_secrets_are_accepted() -> None:
    assert build().app_secret_key == "b" * 64


@pytest.mark.parametrize(
    "value",
    ["CHANGE_ME_WITH_A_LONG_RANDOM_VALUE", "CHANGE_ME" + "x" * 40, "corta"],
)
def test_example_or_short_secret_key_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError, match="APP_SECRET_KEY"):
        build(app_secret_key=value)


def test_example_oxidized_token_is_rejected() -> None:
    with pytest.raises(ValidationError, match="OXIDIZED_SOURCE_TOKEN"):
        build(oxidized_source_token="CHANGE_ME_OXIDIZED_SOURCE_TOKEN")
