import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _fernet(app_secret_key: str) -> Fernet:
    # Deriva una llave Fernet estable de 32 bytes desde APP_SECRET_KEY.
    digest = hashlib.sha256(("oxm-device-secret::" + app_secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(app_secret_key: str, plaintext: str) -> str:
    if plaintext == "":
        return ""
    return _fernet(app_secret_key).encrypt(plaintext.encode()).decode()


def decrypt_secret(app_secret_key: str, stored: str) -> str:
    if stored == "":
        return ""
    try:
        return _fernet(app_secret_key).decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError):
        # Valor heredado en texto plano (aún no migrado): devuélvelo tal cual.
        return stored


def looks_encrypted(app_secret_key: str, stored: str) -> bool:
    if stored == "":
        return True
    try:
        _fernet(app_secret_key).decrypt(stored.encode())
        return True
    except (InvalidToken, ValueError):
        return False
