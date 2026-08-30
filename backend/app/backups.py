import httpx
from fastapi import APIRouter, Depends, Query, Request

from .auth import current_user
from .config import get_settings
from .schemas import BackupEventOut, BackupStatusOut


router = APIRouter(
    prefix="/api/backups",
    tags=["backups"],
    dependencies=[Depends(current_user)],
)


@router.get("/oxidized-status")
async def oxidized_status() -> list[dict]:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{settings.oxidized_url.rstrip('/')}/nodes.json"
            )
            response.raise_for_status()
            nodes = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    return [
        {
            "name": node.get("name"),
            "status": node.get("status"),
            "last": node.get("last"),
        }
        for node in nodes
        if node.get("name") != "phase1-placeholder"
    ]


@router.get("/status", response_model=list[BackupStatusOut])
async def backup_status(request: Request) -> list[dict]:
    return await request.app.state.backup_events.status()


@router.get("/events", response_model=list[BackupEventOut])
async def backup_events(
    request: Request,
    node: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    return await request.app.state.backup_events.list_events(node, limit)
