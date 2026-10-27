import base64
import hashlib
import logging
import re

from cryptography.fernet import Fernet, InvalidToken


log = logging.getLogger("oxidized-ai-manager.secretos")

# Forma de un token Fernet: base64 urlsafe que empieza por el byte de versión
# 0x80 y un sello de tiempo (de ahí «gAAAAA»). El más corto posible mide 100
# caracteres. Una clave de router real no tiene esta forma.
_FERNET_SHAPE = re.compile(r"^gAAAAA[A-Za-z0-9_\-]{90,}={0,2}$")


class ClaveIlegibleError(Exception):
    """El valor está cifrado, pero no con la APP_SECRET_KEY actual.

    Pasa al rotar la llave sin volver a guardar las claves. Tratarlo como texto
    plano heredado entregaba el cifrado a Oxidized como si fuera la clave, y la
    migración al arrancar lo volvía a cifrar encima: el valor original quedaba
    irrecuperable aunque se restaurara la llave anterior.
    """


def _fernet(app_secret_key: str) -> Fernet:
    # Deriva una llave Fernet estable de 32 bytes desde APP_SECRET_KEY.
    digest = hashlib.sha256(("oxm-device-secret::" + app_secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def parece_cifrado(stored: str) -> bool:
    """¿Tiene forma de token Fernet, se pueda descifrar o no?"""
    return bool(_FERNET_SHAPE.match(stored))


def encrypt_secret(app_secret_key: str, plaintext: str) -> str:
    if plaintext == "":
        return ""
    return _fernet(app_secret_key).encrypt(plaintext.encode()).decode()


def decrypt_secret(app_secret_key: str, stored: str) -> str:
    """Descifra un valor guardado.

    Un valor sin forma de token es texto plano heredado (aún no migrado) y se
    devuelve tal cual. Uno con forma de token que no descifra lanza
    `ClaveIlegibleError`: nunca se devuelve el cifrado como si fuera la clave.
    """
    if stored == "":
        return ""
    try:
        return _fernet(app_secret_key).decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError):
        if parece_cifrado(stored):
            log.error(
                "Hay un secreto cifrado con otra APP_SECRET_KEY: no se puede "
                "descifrar. ¿Se cambió la llave? Restaure la anterior o vuelva "
                "a guardar las claves."
            )
            raise ClaveIlegibleError(
                "secreto cifrado con otra APP_SECRET_KEY"
            ) from None
        return stored


def looks_encrypted(app_secret_key: str, stored: str) -> bool:
    """¿Hay que dejar el valor como está al migrar texto plano?

    Un token que no descifra con la llave actual cuenta como cifrado: volver a
    cifrarlo lo destruiría.
    """
    if stored == "":
        return True
    try:
        _fernet(app_secret_key).decrypt(stored.encode())
        return True
    except (InvalidToken, ValueError):
        if parece_cifrado(stored):
            log.error(
                "Secreto cifrado con otra APP_SECRET_KEY: se deja intacto, sin "
                "volver a cifrarlo."
            )
            return True
        return False
