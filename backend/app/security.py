import datetime as dt
from dataclasses import dataclass

import bcrypt
import jwt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# Hash de relleno para cuando el usuario no existe: comprobar la clave contra
# él tarda lo mismo que contra uno real, así el tiempo de respuesta no delata
# qué nombres de usuario existen.
_DUMMY_HASH = hash_password("cuenta-inexistente-relleno")


def burn_password_check(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


@dataclass(frozen=True)
class TokenClaims:
    username: str
    # Versión de sesión de la cuenta. Cambiarla en la base revoca de golpe
    # todos los tokens emitidos antes (cambio de clave, cierre de sesión).
    version: int


def create_access_token(
    username: str, secret: str, ttl_minutes: int, version: int = 0
) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": username,
        "ver": version,
        "iat": now,
        "exp": now + dt.timedelta(minutes=ttl_minutes),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_claims(token: str, secret: str) -> TokenClaims | None:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    subject = payload.get("sub")
    if not isinstance(subject, str):
        return None
    # Los tokens emitidos antes de existir `ver` cuentan como versión 0, que es
    # el valor inicial de la columna: las sesiones abiertas sobreviven al
    # despliegue.
    version = payload.get("ver", 0)
    if not isinstance(version, int) or isinstance(version, bool):
        return None
    return TokenClaims(username=subject, version=version)


def decode_access_token(token: str, secret: str) -> str | None:
    claims = decode_access_claims(token, secret)
    return claims.username if claims else None
