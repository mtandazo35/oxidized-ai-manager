from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .auth import current_user
from .scheduler import mask_remote_url
from .schemas import SettingsOut, SettingsUpdate


router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(current_user)],
)

ALLOWED_URL_PREFIXES = ("https://", "http://", "ssh://", "git@")


async def _settings_out(request: Request) -> dict:
    values = await request.app.state.settings.get_all()
    try:
        interval = int(values["backup_interval_minutes"])
    except ValueError:
        interval = 60
    try:
        push_interval = int(values["git_push_interval_minutes"])
    except ValueError:
        push_interval = 60
    last_push_at = None
    if values["last_push_at"]:
        try:
            last_push_at = datetime.fromisoformat(values["last_push_at"])
        except ValueError:
            last_push_at = None
    return {
        "backup_interval_minutes": interval,
        "git_remote_enabled": values["git_remote_enabled"] == "true",
        "git_remote_url": mask_remote_url(values["git_remote_url"]),
        "git_push_interval_minutes": push_interval,
        "last_push_ok": {"true": True, "false": False}.get(values["last_push_ok"]),
        "last_push_at": last_push_at,
        "last_push_detail": values["last_push_detail"],
    }


@router.get("", response_model=SettingsOut)
async def get_settings_view(request: Request) -> dict:
    return await _settings_out(request)


@router.put("", response_model=SettingsOut)
async def update_settings_view(request: Request, payload: SettingsUpdate) -> dict:
    stored = await request.app.state.settings.get_all()
    url = payload.git_remote_url.strip()
    if ":***@" in url:
        url = stored["git_remote_url"]
    if url and not url.startswith(ALLOWED_URL_PREFIXES):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="La URL del remoto debe iniciar con https://, ssh:// o git@.",
        )
    if payload.git_remote_enabled and not url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Configure la URL del remoto antes de habilitar el envío.",
        )
    await request.app.state.settings.set_many(
        {
            "backup_interval_minutes": str(payload.backup_interval_minutes),
            "git_remote_enabled": "true" if payload.git_remote_enabled else "false",
            "git_remote_url": url,
            "git_push_interval_minutes": str(payload.git_push_interval_minutes),
        }
    )
    return await _settings_out(request)
