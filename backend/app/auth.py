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
    user = await request.app.state.users.get_by_username(form.username)
    if user is None or not verify_password(form.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(
        user["username"],
        settings.app_secret_key,
        settings.access_token_ttl_minutes,
    )
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
async def me(username: str = Depends(current_user)) -> dict:
    return {"username": username}


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
