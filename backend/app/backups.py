import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .auth import current_user
from .config import get_settings
from .gitrepo import (
    GitRepoError,
    NotFoundInRepoError,
    list_versions,
    show_config,
    show_diff,
)
from .scheduler import trigger_node_backup
from .schemas import (
    DEVICE_NAME_PATTERN,
    BackupEventOut,
    BackupStatusOut,
    BulkBackupRequest,
)

COMMIT_QUERY_PATTERN = r"^[0-9a-f]{6,40}$"

router = APIRouter(
    prefix="/api/backups",
    tags=["backups"],
    dependencies=[Depends(current_user)],
)


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def run_bulk_backup(request: Request, payload: BulkBackupRequest) -> dict:
    if payload.scope == "group" and not payload.group:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Indique el grupo a respaldar.",
        )
    if payload.scope == "devices" and not payload.device_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Seleccione al menos un router.",
        )
    devices = await request.app.state.devices.list_devices()
    targets = [device for device in devices if device["enabled"]]
    if payload.scope == "group":
        targets = [
            device for device in targets
            if device.get("group_name", "") == payload.group
        ]
    elif payload.scope == "devices":
        wanted = set(payload.device_ids)
        targets = [device for device in targets if device["id"] in wanted]
    settings = get_settings()
    queued: list[str] = []
    failed: list[str] = []
    for device in targets:
        try:
            await trigger_node_backup(settings.oxidized_url, device["name"])
            queued.append(device["name"])
        except httpx.HTTPError:
            failed.append(device["name"])
    return {"queued": queued, "failed": failed}


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


@router.get("/versions")
async def backup_versions(
    node: str = Query(pattern=DEVICE_NAME_PATTERN),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict]:
    settings = get_settings()
    try:
        return await list_versions(settings.oxidized_backup_repo, node, limit)
    except NotFoundInRepoError:
        return []
    except GitRepoError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo leer el repositorio de respaldos: {error}",
        )


@router.get("/diff")
async def backup_diff(
    node: str = Query(pattern=DEVICE_NAME_PATTERN),
    commit: str = Query(pattern=COMMIT_QUERY_PATTERN),
) -> dict:
    settings = get_settings()
    try:
        diff = await show_diff(settings.oxidized_backup_repo, node, commit)
    except NotFoundInRepoError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Versión no encontrada.",
        )
    except GitRepoError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo leer el repositorio de respaldos: {error}",
        )
    return {"node": node, "commit": commit, "diff": diff}


@router.get("/config")
async def backup_config(
    node: str = Query(pattern=DEVICE_NAME_PATTERN),
    commit: str = Query(pattern=COMMIT_QUERY_PATTERN),
) -> dict:
    settings = get_settings()
    try:
        content = await show_config(settings.oxidized_backup_repo, node, commit)
    except NotFoundInRepoError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Versión no encontrada.",
        )
    except GitRepoError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo leer el repositorio de respaldos: {error}",
        )
    return {"node": node, "commit": commit, "config": content}
