import time

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


async def current_user(request: Request, token: str = Depends(oauth2_scheme)) -> str:
    settings = get_settings()
    username = decode_access_token(token, settings.app_secret_key)
    if username:
        user = await request.app.state.users.get_by_username(username)
        if user:
            return username
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


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
async def me(request: Request, username: str = Depends(current_user)) -> dict:
    user = await request.app.state.users.get_by_username(username)
    return {
        "username": username,
        "must_change_password": bool(user and user.get("must_change_password")),
    }


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    username: str = Depends(current_user),
) -> None:
    user = await request.app.state.users.get_by_username(username)
    if user is None or not verify_password(
        payload.current_password, user["password_hash"]
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Current password is incorrect.",
        )
    await request.app.state.users.update_password(
        username, hash_password(payload.new_password)
    )
