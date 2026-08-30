from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import create_pool, init_schema
from .devices import router as devices_router
from .health import check_dependencies
from .oxidized_source import router as oxidized_router
from .repository import DeviceRepository


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pool = await create_pool(settings)
    await init_schema(pool)
    app.state.devices = DeviceRepository(pool)
    try:
        yield
    finally:
        await pool.close()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Foundation API for Oxidized AI Manager.",
    lifespan=lifespan,
)

app.include_router(devices_router)
app.include_router(oxidized_router)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
    }


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
