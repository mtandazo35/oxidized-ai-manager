"""API de auditoría (Fase 4).

Lee configuraciones ya respaldadas de `backups.git` y las pasa por el motor de
reglas. Es estrictamente de lectura: no abre sesión contra ningún router ni
propone escrituras. El repositorio está montado de solo lectura en el backend.
"""

import asyncio
import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .audit_rules import SEVERITY_ORDER, audit_config, rule_catalog
from .auth import CurrentUser, require_audit
from .config import get_settings
from .gitrepo import GitRepoError, NotFoundInRepoError, show_config
from .schemas import DEVICE_NAME_PATTERN, AuditReport, AuditSummaryRow

log = logging.getLogger("oxidized-ai-manager.audit")

# Límite de auditorías en paralelo: cada una lanza un `git show`, y el resumen
# recorre todo el inventario.
CONCURRENCY = 8

COMMIT_QUERY_PATTERN = r"^(?:[0-9a-f]{6,40}|HEAD)$"

router = APIRouter(
    prefix="/api/audit",
    tags=["audit"],
    dependencies=[Depends(require_audit)],
)


@router.get("/rules")
async def audit_rules() -> dict:
    """Catálogo de reglas evaluadas, para que los hallazgos sean auditables."""
    return {"rules": rule_catalog()}


async def _audit_node(
    repo_path: str, node: str, commit: str, since: dt.datetime | None = None
) -> dict:
    # `since` oculta los respaldos del dueño anterior del nombre del nodo.
    config_text = await show_config(repo_path, node, commit, since=since)
    report = audit_config(config_text)
    return {"node": node, "commit": commit, **report}


@router.get("/node", response_model=AuditReport)
async def audit_node(
    request: Request,
    node: str = Query(pattern=DEVICE_NAME_PATTERN),
    commit: str = Query(default="HEAD", pattern=COMMIT_QUERY_PATTERN),
    user: CurrentUser = Depends(require_audit),
) -> dict:
    device = await request.app.state.devices.get_device_by_name(node, user.scope)
    if device is None and user.scope is not None:
        # 404 y no 403: un 403 confirmaría que el equipo existe en otra
        # empresa.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Equipo no encontrado."
        )
    since = device.get("history_since") if device else None
    settings = get_settings()
    try:
        return await _audit_node(
            settings.oxidized_backup_repo, node, commit, since=since
        )
    except NotFoundInRepoError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No hay respaldo de ese equipo en esa versión.",
        )
    except GitRepoError as error:
        # El detalle de git trae rutas internas: al log, no al cliente.
        log.warning("Error leyendo el repositorio de respaldos: %s", error)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer el repositorio de respaldos.",
        )


@router.get("/summary", response_model=list[AuditSummaryRow])
async def audit_summary(
    request: Request, user: CurrentUser = Depends(require_audit)
) -> list[dict]:
    """Audita el último respaldo de cada equipo visible para el usuario."""
    settings = get_settings()
    devices = await request.app.state.devices.list_devices(user.scope)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def row(device: dict) -> dict:
        base = {
            "node": device["name"],
            "group_name": device.get("group_name", ""),
            "identity": device.get("identity", ""),
            "score": 0,
            "level": "unknown",
            "counts": {severity: 0 for severity in SEVERITY_ORDER},
            "ros_version": device.get("ros_version", ""),
            "error": "",
        }
        async with semaphore:
            try:
                report = await _audit_node(
                    settings.oxidized_backup_repo,
                    device["name"],
                    "HEAD",
                    since=device.get("history_since"),
                )
            except NotFoundInRepoError:
                return {**base, "error": "sin respaldo todavía"}
            except (GitRepoError, OSError) as error:
                log.warning("Auditoría de %s fallida: %s", device["name"], error)
                return {**base, "error": "no se pudo leer el respaldo"}
        return {
            **base,
            "score": report["score"],
            "level": report["level"],
            "counts": report["counts"],
            "ros_version": report["ros_version"] or base["ros_version"],
        }

    rows = await asyncio.gather(*(row(device) for device in devices))
    return sorted(rows, key=lambda item: (-item["score"], item["node"]))
