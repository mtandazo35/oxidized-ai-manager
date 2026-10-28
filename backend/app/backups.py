import asyncio
import datetime as dt
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .auth import CurrentUser, current_user, require_write
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

router = APIRouter(prefix="/api/backups", tags=["backups"])

log = logging.getLogger("oxidized-ai-manager.backups")

# Lo que ve el cliente cuando git falla. El detalle (rutas internas del
# servidor) va solo al log.
REPO_ERROR_DETAIL = "No se pudo leer el repositorio de respaldos."


def repo_error(error: Exception) -> HTTPException:
    log.warning("Error leyendo el repositorio de respaldos: %s", error)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail=REPO_ERROR_DETAIL
    )


async def _allowed_nodes(request: Request, user: CurrentUser) -> list[str] | None:
    """Nombres de nodo visibles, o `None` si el usuario ve todo."""
    if user.scope is None:
        return None
    devices = await request.app.state.devices.list_devices(user.scope)
    return [device["name"] for device in devices]


async def _authorize_node(
    request: Request, user: CurrentUser, node: str
) -> dt.datetime | None:
    """Rechaza con 404 un nodo que no pertenece a la empresa del usuario.

    Se responde 404 y no 403 a propósito: un 403 confirmaría que ese equipo
    existe en otra empresa.

    Devuelve desde cuándo es visible el historial del nodo (`history_since`):
    el nombre pudo ser antes de otro equipo, de esta u otra empresa, y lo
    anterior a esa fecha no le pertenece a quien pregunta. `None` = todo.
    """
    device = await request.app.state.devices.get_device_by_name(node, user.scope)
    if device is None:
        if user.scope is None:
            # El admin puede consultar nodos que ya no están en el inventario.
            return None
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Equipo no encontrado."
        )
    return device.get("history_since")


# Límite del chequeo de alcance: cuánto espera cada sondeo TCP y cuántos a la
# vez. El objetivo es decidir rápido si un equipo "está ahí", no abrir SSH.
CHECK_TIMEOUT = 3.0
CHECK_CONCURRENCY = 50


async def _resolve_targets(
    request: Request, user: CurrentUser, payload: BulkBackupRequest
) -> list[dict]:
    """Equipos activos que abarca la petición (todos / grupo / selección)."""
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
    return targets


async def _tcp_alcanzable(address: str, port: int) -> bool:
    """¿Abre el puerto? Sondeo TCP corto; no autentica, solo mide alcance."""
    try:
        fut = asyncio.open_connection(address, port)
        reader, writer = await asyncio.wait_for(fut, timeout=CHECK_TIMEOUT)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except (OSError, asyncio.TimeoutError):
        return False


@router.post("/check")
async def check_reachability(
    request: Request,
    payload: BulkBackupRequest,
    user: CurrentUser = Depends(require_write),
) -> dict:
    """Antes de respaldar: prueba el alcance TCP de cada equipo en paralelo.

    Sirve para no gastar el timeout SSH (30 s por nodo) en equipos caídos:
    el panel avisa en un modal y respalda solo los que responden.
    """
    targets = await _resolve_targets(request, user, payload)
    sem = asyncio.Semaphore(CHECK_CONCURRENCY)

    async def comprobar(device: dict) -> tuple[dict, bool]:
        async with sem:
            ok = await _tcp_alcanzable(device["address"], int(device["port"]))
        return device, ok

    resultados = await asyncio.gather(*(comprobar(d) for d in targets))
    reachable = [
        {"id": d["id"], "name": d["name"]} for d, ok in resultados if ok
    ]
    unreachable = [
        {"id": d["id"], "name": d["name"], "address": d["address"],
         "port": d["port"], "group_name": d.get("group_name", "")}
        for d, ok in resultados if not ok
    ]
    return {
        "checked": len(targets),
        "reachable": reachable,
        "unreachable": unreachable,
    }


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def run_bulk_backup(
    request: Request,
    payload: BulkBackupRequest,
    user: CurrentUser = Depends(require_write),
) -> dict:
    targets = await _resolve_targets(request, user, payload)
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
    since = await _authorize_node(request, user, node)
    settings = get_settings()
    try:
        return await list_versions(
            settings.oxidized_backup_repo, node, limit, since=since
        )
    except NotFoundInRepoError:
        return []
    except GitRepoError as error:
        raise repo_error(error)


@router.get("/diff")
async def backup_diff(
    request: Request,
    node: str = Query(pattern=DEVICE_NAME_PATTERN),
    commit: str = Query(pattern=COMMIT_QUERY_PATTERN),
    user: CurrentUser = Depends(current_user),
) -> dict:
    since = await _authorize_node(request, user, node)
    settings = get_settings()
    try:
        diff = await show_diff(
            settings.oxidized_backup_repo, node, commit, since=since
        )
    except NotFoundInRepoError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Versión no encontrada.",
        )
    except GitRepoError as error:
        raise repo_error(error)
    return {"node": node, "commit": commit, "diff": diff}


@router.get("/config")
async def backup_config(
    request: Request,
    node: str = Query(pattern=DEVICE_NAME_PATTERN),
    commit: str = Query(pattern=COMMIT_QUERY_PATTERN),
    user: CurrentUser = Depends(current_user),
) -> dict:
    since = await _authorize_node(request, user, node)
    settings = get_settings()
    try:
        content = await show_config(
            settings.oxidized_backup_repo, node, commit, since=since
        )
    except NotFoundInRepoError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Versión no encontrada.",
        )
    except GitRepoError as error:
        raise repo_error(error)
    return {"node": node, "commit": commit, "config": content}
