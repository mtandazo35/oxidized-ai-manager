from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from .config import get_settings
from .health import check_dependencies


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Foundation API for Oxidized AI Manager.",
)


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
