import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, status
from fastapi.responses import FileResponse, JSONResponse

from .auth import router as auth_router
from .backups import router as backups_router
from .config import get_settings
from .db import create_pool, init_schema
from .devices import router as devices_router
from .health import check_dependencies
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


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pool = await create_pool(settings)
    await init_schema(pool)
    devices = DeviceRepository(pool)
    app.state.devices = devices
    app.state.backup_events = BackupEventRepository(pool)
    app.state.settings = SettingsRepository(pool)
    users = UserRepository(pool)
    app.state.users = users
    await devices.encrypt_legacy_passwords()
    if await users.count_users() == 0 and settings.admin_password:
        await users.create_user(
            settings.admin_username, hash_password(settings.admin_password)
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

app.include_router(auth_router)
app.include_router(backups_router)
app.include_router(devices_router)
app.include_router(oxidized_router)
app.include_router(settings_router)


PANEL_FILE = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    return FileResponse(PANEL_FILE, media_type="text/html")


@app.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness() -> JSONResponse:
    checks = await check_dependencies(settings)
    ready = all(checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ok" if ready else "degraded", "checks": checks},
    )
