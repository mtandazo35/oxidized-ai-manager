import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .auth import CurrentUser, client_ip, current_user, require_write
from .config import get_settings
from .credentials import extract_pppoe
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

router = APIRouter(prefix="/api/backups", tags=["backups"])


async def _allowed_nodes(request: Request, user: CurrentUser) -> list[str] | None:
    """Nombres de nodo visibles, o `None` si el usuario ve todo."""
    if user.scope is None:
        return None
    devices = await request.app.state.devices.list_devices(user.scope)
    return [device["name"] for device in devices]


async def _authorize_node(request: Request, user: CurrentUser, node: str) -> None:
    """Rechaza con 404 un nodo que no pertenece a la empresa del usuario.

    Se responde 404 y no 403 a propósito: un 403 confirmaría que ese equipo
    existe en otra empresa.
    """
    if user.scope is None:
        return
    device = await request.app.state.devices.get_device_by_name(node, user.scope)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Equipo no encontrado."
        )


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def run_bulk_backup(
    request: Request,
    payload: BulkBackupRequest,
    user: CurrentUser = Depends(require_write),
) -> dict:
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
    devices = await request.app.state.devices.list_devices(user.scope)
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
async def oxidized_status(
    request: Request, user: CurrentUser = Depends(current_user)
) -> list[dict]:
    allowed = await _allowed_nodes(request, user)
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
        and (allowed is None or node.get("name") in allowed)
    ]


@router.get("/status", response_model=list[BackupStatusOut])
async def backup_status(
    request: Request, user: CurrentUser = Depends(current_user)
) -> list[dict]:
    allowed = await _allowed_nodes(request, user)
    return await request.app.state.backup_events.status(allowed)


@router.get("/events", response_model=list[BackupEventOut])
async def backup_events(
    request: Request,
    node: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=500),
    user: CurrentUser = Depends(current_user),
) -> list[dict]:
    if node is not None:
        await _authorize_node(request, user, node)
    allowed = await _allowed_nodes(request, user)
    return await request.app.state.backup_events.list_events(node, limit, allowed)


@router.get("/versions")
async def backup_versions(
    request: Request,
    node: str = Query(pattern=DEVICE_NAME_PATTERN),
    limit: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(current_user),
) -> list[dict]:
    await _authorize_node(request, user, node)
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
    request: Request,
    node: str = Query(pattern=DEVICE_NAME_PATTERN),
    commit: str = Query(pattern=COMMIT_QUERY_PATTERN),
    user: CurrentUser = Depends(current_user),
) -> dict:
    await _authorize_node(request, user, node)
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


@router.get("/pppoe")
async def backup_pppoe(
    request: Request,
    node: str = Query(pattern=DEVICE_NAME_PATTERN),
    commit: str = Query(default="HEAD", pattern=r"^(?:[0-9a-f]{6,40}|HEAD)$"),
    user: CurrentUser = Depends(require_write),
) -> dict:
    """Usuarios y claves PPPoE del respaldo de ese equipo.

    Se limita a los roles que pueden operar (`admin` y `operador`): son
    credenciales de clientes finales, no información de consulta general. Cada
    consulta queda anotada en la bitácora, porque leer contraseñas de clientes
    es exactamente el tipo de acción que hay que poder auditar después.
    """
    await _authorize_node(request, user, node)
    settings = get_settings()
    try:
        config_text = await show_config(settings.oxidized_backup_repo, node, commit)
    except NotFoundInRepoError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Todavía no hay respaldo de ese equipo.",
        )
    except GitRepoError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo leer el repositorio de respaldos: {error}",
        )
    resultado = extract_pppoe(config_text)
    await request.app.state.activity.record(
        action="credenciales.pppoe",
        username=user.username,
        ip=client_ip(request),
        target=node,
        detail=f"{resultado['total']} usuarios consultados",
        ok=True,
    )
    return {"node": node, "commit": commit, **resultado}


@router.get("/config")
async def backup_config(
    request: Request,
    node: str = Query(pattern=DEVICE_NAME_PATTERN),
    commit: str = Query(pattern=COMMIT_QUERY_PATTERN),
    user: CurrentUser = Depends(current_user),
) -> dict:
    await _authorize_node(request, user, node)
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
