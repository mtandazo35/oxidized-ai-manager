import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, status
from fastapi.responses import FileResponse, JSONResponse

from .audit import router as audit_router
from .auth import router as auth_router
from .backups import router as backups_router
from .config import get_settings
from .db import create_pool, init_schema
from .devices import router as devices_router
from .health import backup_freshness, check_dependencies
from .middleware import (
    LoginRateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from .oxidized_source import router as oxidized_router
from .repository import (
    BackupEventRepository,
    DeviceRepository,
    SettingsRepository,
    UserRepository,
)
from .scheduler import scheduler_loop
from .security import hash_password
from .settings_api import router as settings_router
from .users_api import router as users_router


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pool = await create_pool(settings)
    await init_schema(pool)
    devices = DeviceRepository(pool)
    app.state.devices = devices
    app.state.backup_events = BackupEventRepository(pool)
    app_settings = SettingsRepository(pool)
    app.state.settings = app_settings
    users = UserRepository(pool)
    app.state.users = users
    await devices.encrypt_legacy_passwords()
    await app_settings.encrypt_legacy_settings()
    if await users.count_users() == 0 and settings.admin_password:
        await users.create_user(
            settings.admin_username,
            hash_password(settings.admin_password),
            must_change_password=True,
            role="admin",
        )
    scheduler_task = asyncio.create_task(scheduler_loop(app, settings))
    try:
        yield
    finally:
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler_task
        await pool.close()


_docs_enabled = settings.app_env == "development"

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Foundation API for Oxidized AI Manager.",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# El orden importa: las cabeceras se añaden a *toda* respuesta, incluida la
# que devuelve el limitador con 429.
app.add_middleware(LoginRateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware, enable_hsts=settings.app_enable_hsts)

app.include_router(audit_router)
app.include_router(auth_router)
app.include_router(backups_router)
app.include_router(devices_router)
app.include_router(oxidized_router)
app.include_router(settings_router)
app.include_router(users_router)


PANEL_FILE = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    return FileResponse(PANEL_FILE, media_type="text/html")


@app.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/backups")
async def backups_health() -> JSONResponse:
    """Frescura de los respaldos, para monitoreo externo (Uptime Kuma).

    Responde 503 cuando algún equipo activo lleva sin respaldo más de
    `backup_staleness_factor` veces su intervalo, para que el monitor alerte
    sin necesidad de credenciales. No revela nombres de equipos.
    """
    summary = await backup_freshness(app, settings)
    return JSONResponse(
        status_code=status.HTTP_200_OK
        if summary["status"] == "ok"
        else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=summary,
    )


@app.get("/health/ready")
async def readiness() -> JSONResponse:
    checks = await check_dependencies(settings)
    ready = all(checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ok" if ready else "degraded", "checks": checks},
    )
