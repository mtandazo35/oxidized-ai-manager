from fastapi import APIRouter, Depends, Query, Request

from .auth import current_user
from .schemas import BackupEventOut, BackupStatusOut


router = APIRouter(
    prefix="/api/backups",
    tags=["backups"],
    dependencies=[Depends(current_user)],
)


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
