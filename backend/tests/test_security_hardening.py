import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.secrets_box import decrypt_secret, encrypt_secret, looks_encrypted


pytestmark = pytest.mark.anyio

KEY = "test-secret-key-0123456789abcdef0123456789abcdef"


def test_encrypt_roundtrip() -> None:
    token = encrypt_secret(KEY, "S3cret!")
    assert token != "S3cret!"
    assert decrypt_secret(KEY, token) == "S3cret!"


def test_empty_stays_empty() -> None:
    assert encrypt_secret(KEY, "") == ""
    assert decrypt_secret(KEY, "") == ""


def test_legacy_plaintext_is_returned_as_is() -> None:
    assert decrypt_secret(KEY, "plaintext-legacy") == "plaintext-legacy"
    assert looks_encrypted(KEY, "plaintext-legacy") is False
    assert looks_encrypted(KEY, encrypt_secret(KEY, "x")) is True


def test_wrong_key_cannot_decrypt() -> None:
    token = encrypt_secret(KEY, "S3cret!")
    other = decrypt_secret("otra-llave-distinta-totalmente-diferente", token)
    assert other != "S3cret!"


def client() -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url="http://test")


async def login(api, username, password):
    return await api.post(
        "/api/auth/login", data={"username": username, "password": password}
    )


async def test_bruteforce_lockout(user_repository) -> None:
    from app.auth import _failed_logins
    _failed_logins.clear()
    async with client() as api:
        for _ in range(8):
            await login(api, "admin", "wrong")
        locked = await login(api, "admin", "wrong")
        # incluso con la clave correcta queda bloqueada mientras dura el lockout
        blocked_ok = await login(api, "admin", "admin-test-password")

    assert locked.status_code == 429
    assert blocked_ok.status_code == 429
    _failed_logins.clear()


async def test_successful_login_resets_counter(user_repository) -> None:
    from app.auth import _failed_logins
    _failed_logins.clear()
    async with client() as api:
        for _ in range(3):
            await login(api, "admin", "wrong")
        good = await login(api, "admin", "admin-test-password")

    assert good.status_code == 200
    assert "admin" not in _failed_logins


async def test_oxidized_nodes_receive_decrypted_password(
    auth_headers, device_repository
) -> None:
    async with client() as api:
        await api.post(
            "/api/devices",
            headers=auth_headers,
            json={"name": "rb-sec", "address": "192.0.2.1",
                  "username": "u", "password": "PlainClave"},
        )
    # el fake almacena tal cual; el endpoint real cifra/descifra en repo real.
    nodes = await device_repository.list_oxidized_nodes()
    assert nodes[0]["password"] == "PlainClave"
