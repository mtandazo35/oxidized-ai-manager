import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

from .config import get_settings
from .schemas import ChangePasswordRequest, TokenResponse
from .security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


router = APIRouter(prefix="/api/auth", tags=["auth"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Bloqueo de fuerza bruta por cuenta (complementa el rate-limit por IP de nginx).
LOCKOUT_THRESHOLD = 8
LOCKOUT_SECONDS = 300
_failed_logins: dict[str, list[float]] = {}


def _is_locked(username: str) -> bool:
    attempts = _failed_logins.get(username, [])
    recent = [t for t in attempts if time.monotonic() - t < LOCKOUT_SECONDS]
    _failed_logins[username] = recent
    return len(recent) >= LOCKOUT_THRESHOLD


def _record_failure(username: str) -> None:
    _failed_logins.setdefault(username, []).append(time.monotonic())


def _reset_failures(username: str) -> None:
    _failed_logins.pop(username, None)


@dataclass(frozen=True)
class CurrentUser:
    """Quién pide y con qué alcance.

    El rol y la empresa se leen de la base en cada petición, no del token: así
    un cambio de rol o de empresa aplica al instante y no espera a que caduque
    el JWT (8 h).
    """

    username: str
    role: str
    group_name: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def scope(self) -> str | None:
        """Empresa por la que filtrar; `None` significa "todas".

        Solo el administrador obtiene `None`. Una cuenta no administradora sin
        empresa asignada recibe `""`, que **no coincide con ningún equipo** de
        un inventario con empresas: falla cerrada a propósito.
        """
        return None if self.is_admin else self.group_name

    def can_write(self) -> bool:
        return self.role in ("admin", "operador")

    def can_audit(self) -> bool:
        return self.role in ("admin", "operador", "auditor")


async def current_user(
    request: Request, token: str = Depends(oauth2_scheme)
) -> CurrentUser:
    settings = get_settings()
    username = decode_access_token(token, settings.app_secret_key)
    if username:
        user = await request.app.state.users.get_by_username(username)
        if user:
            return CurrentUser(
                username=username,
                role=user.get("role") or "lector",
                group_name=user.get("group_name") or "",
            )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


async def require_write(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """Alta, edición, borrado y disparo de respaldos."""
    if not user.can_write():
        raise _forbidden("Su cuenta es de solo lectura.")
    return user


async def require_audit(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not user.can_audit():
        raise _forbidden("Su cuenta no tiene acceso a la auditoría.")
    return user


async def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """Ajustes globales, usuarios y recarga de Oxidized."""
    if not user.is_admin:
        raise _forbidden("Solo un administrador puede hacer esto.")
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request, form: OAuth2PasswordRequestForm = Depends()
) -> dict:
    settings = get_settings()
    if _is_locked(form.username):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Cuenta bloqueada temporalmente por intentos fallidos. Espere unos minutos.",
        )
    user = await request.app.state.users.get_by_username(form.username)
    if user is None or not verify_password(form.password, user["password_hash"]):
        _record_failure(form.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _reset_failures(form.username)
    token = create_access_token(
        user["username"],
        settings.app_secret_key,
        settings.access_token_ttl_minutes,
    )
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
async def me(request: Request, user: CurrentUser = Depends(current_user)) -> dict:
    stored = await request.app.state.users.get_by_username(user.username)
    return {
        "username": user.username,
        "role": user.role,
        "group_name": user.group_name,
        "can_write": user.can_write(),
        "can_audit": user.can_audit(),
        "is_admin": user.is_admin,
        "must_change_password": bool(stored and stored.get("must_change_password")),
    }


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    user: CurrentUser = Depends(current_user),
) -> None:
    stored = await request.app.state.users.get_by_username(user.username)
    if stored is None or not verify_password(
        payload.current_password, stored["password_hash"]
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Current password is incorrect.",
        )
    await request.app.state.users.update_password(
        user.username, hash_password(payload.new_password)
    )
