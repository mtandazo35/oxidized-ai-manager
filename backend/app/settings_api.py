from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .auth import require_admin
from .config import get_settings
from .notifications import send_telegram
from .scheduler import mask_remote_url, push_backups
from .schemas import SettingsOut, SettingsUpdate

# El token del bot se devuelve enmascarado, igual que la URL del remoto: un
# volcado de la base o una captura del panel no deben revelarlo.
TOKEN_MASK = "***"


def _mask_token(token: str) -> str:
    return TOKEN_MASK if token else ""


# Los ajustes son globales (intervalo de respaldo, Git remoto): solo admin.
router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(require_admin)],
)

# Sin `http://`: el token viajaría en claro junto con todas las
# configuraciones de los routers.
ALLOWED_URL_PREFIXES = ("https://", "ssh://", "git@")


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
    last_push_at = _fecha(values["last_push_at"])
    try:
        alert_hour = int(values["stale_alert_hour"])
    except ValueError:
        alert_hour = 8
    try:
        alert_threshold = int(values["stale_alert_threshold_hours"])
    except ValueError:
        alert_threshold = 24
    return {
        "backup_interval_minutes": interval,
        "git_remote_enabled": values["git_remote_enabled"] == "true",
        "git_remote_url": mask_remote_url(values["git_remote_url"]),
        "git_push_interval_minutes": push_interval,
        "last_push_ok": {"true": True, "false": False}.get(values["last_push_ok"]),
        "last_push_at": last_push_at,
        "last_push_detail": values["last_push_detail"],
        "telegram_enabled": values["telegram_enabled"] == "true",
        "telegram_bot_token": _mask_token(values["telegram_bot_token"]),
        "telegram_chat_id": values["telegram_chat_id"],
        "stale_alert_hour": alert_hour,
        "stale_alert_threshold_hours": alert_threshold,
        "last_stale_alert_at": _fecha(values["last_stale_alert_at"]),
        "last_stale_alert_detail": values["last_stale_alert_detail"],
    }


def _fecha(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@router.get("", response_model=SettingsOut)
async def get_settings_view(request: Request) -> dict:
    return await _settings_out(request)


@router.put("", response_model=SettingsOut)
async def update_settings_view(request: Request, payload: SettingsUpdate) -> dict:
    stored = await request.app.state.settings.get_all()
    url = payload.git_remote_url.strip()
    if "***" in url:
        # El panel recibe la URL con el token oculto y la devuelve tal cual si
        # nadie la toca. Solo entonces se conserva la guardada: si cambió el
        # servidor o la ruta, reutilizar el token lo enviaría a otro destino.
        if url != mask_remote_url(stored["git_remote_url"]):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Cambió la URL del remoto: escriba de nuevo el token "
                "completo en lugar de ***.",
            )
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
    # El token del bot llega enmascarado si nadie lo tocó: conservar el guardado.
    token = payload.telegram_bot_token.strip()
    if token == TOKEN_MASK:
        token = stored["telegram_bot_token"]
    if payload.telegram_enabled and (not token or not payload.telegram_chat_id.strip()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Configure el token del bot y el chat_id antes de activar "
            "los avisos de Telegram.",
        )
    await request.app.state.settings.set_many(
        {
            "backup_interval_minutes": str(payload.backup_interval_minutes),
            "git_remote_enabled": "true" if payload.git_remote_enabled else "false",
            "git_remote_url": url,
            "git_push_interval_minutes": str(payload.git_push_interval_minutes),
            "telegram_enabled": "true" if payload.telegram_enabled else "false",
            "telegram_bot_token": token,
            "telegram_chat_id": payload.telegram_chat_id.strip(),
            "stale_alert_hour": str(payload.stale_alert_hour),
            "stale_alert_threshold_hours": str(payload.stale_alert_threshold_hours),
        }
    )
    return await _settings_out(request)


@router.post("/test-git")
async def test_git(request: Request) -> dict:
    """Prueba la conexión al Git remoto con lo guardado: hace un push real, que
    es lo que confirma a la vez el acceso y el permiso de escritura (el error
    típico es un token sin «Contents: Read and write»)."""
    values = await request.app.state.settings.get_all()
    url = values["git_remote_url"]
    if not url:
        return {"ok": False, "detail": "No hay una URL de Git configurada."}
    ok, detail = await push_backups(get_settings().oxidized_backup_repo, url)
    return {"ok": ok, "detail": detail}


@router.post("/test-telegram")
async def test_telegram(request: Request) -> dict:
    """Envía un mensaje de prueba con el token y chat_id guardados."""
    values = await request.app.state.settings.get_all()
    ok, detail = await send_telegram(
        values["telegram_bot_token"],
        values["telegram_chat_id"],
        "✅ Prueba de Oxidized AI Manager: los avisos de respaldo están "
        "configurados correctamente.",
    )
    return {"ok": ok, "detail": detail}
