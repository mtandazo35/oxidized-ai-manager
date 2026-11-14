import secrets
import time

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)

from .auth import require_admin
from .backups import ajustar_credencial_activa
from .config import get_settings
from .gitrepo import GitRepoError, NotFoundInRepoError, list_versions, show_config
from .integrity import revisar
from .metadata import parse_routeros_metadata
from .schemas import BackupEventIn, OxidizedNode
from .scheduler import refresh_inventory, trigger_node_backup
from .secrets_box import ClaveIlegibleError


router = APIRouter(prefix="/api/oxidized", tags=["oxidized"])

# Oxidized aborts when the source yields zero nodes, so an empty inventory
# still returns the inert phase-1 placeholder (interval 0, no credentials).
PLACEHOLDER_NODE = {
    "name": "phase1-placeholder",
    "ip": "127.0.0.1",
    "ssh_port": 22,
    "model": "routeros",
    "username": "",
    "password": "",
}


async def _revisar_integridad(request: Request, node: str, config_text: str) -> None:
    """Guarda el veredicto de integridad del respaldo que acaba de entrar.

    No propaga errores a propósito: llegados aquí el respaldo ya está commiteado
    y el evento ya quedó registrado, así que perder el veredicto es preferible a
    devolverle un 500 a Oxidized, que daría el nodo por fallido y lo reintentaría
    sin necesidad.
    """
    settings = get_settings()
    repo = settings.oxidized_backup_repo
    try:
        device = await request.app.state.devices.get_device_by_name(node)
        modelo = (device or {}).get("model") or "routeros"
        # Solo se compara con versiones de este mismo equipo: si el nombre se
        # reutilizó, lo anterior a `history_since` es de otro dueño y daría
        # avisos falsos («se encogió»…) calculados contra su configuración.
        desde = (device or {}).get("history_since")
        versiones = await list_versions(repo, node, 2, since=desde)
        anterior = None
        if len(versiones) > 1:
            anterior = await show_config(repo, node, versiones[1]["commit"], since=desde)
        veredicto = revisar(config_text, anterior, routeros=modelo == "routeros")
        await request.app.state.backup_integrity.record(
            node,
            versiones[0]["commit"] if versiones else "",
            veredicto.tamano,
            veredicto.lineas,
            veredicto.secciones,
            list(veredicto.avisos),
        )
    except (GitRepoError, NotFoundInRepoError, OSError, ValueError, KeyError):
        return


def _require_token(x_oxidized_token: str) -> None:
    settings = get_settings()
    # En bytes: `compare_digest` con un `str` no ASCII lanza TypeError, y una
    # cabecera con un acento acababa en 500 en lugar de 401.
    if not secrets.compare_digest(
        x_oxidized_token.encode("utf-8", "surrogateescape"),
        settings.oxidized_source_token.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de origen ausente o no válido.",
        )


@router.get("/nodes", response_model=list[OxidizedNode])
async def oxidized_nodes(
    request: Request,
    x_oxidized_token: str = Header(default=""),
) -> list[dict]:
    _require_token(x_oxidized_token)
    try:
        devices = await request.app.state.devices.list_oxidized_nodes()
    except ClaveIlegibleError:
        # Mejor que Oxidized no reciba inventario a que reciba el cifrado como
        # si fuera la clave e intente entrar con él en cada router.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pueden descifrar las claves de los equipos: "
            "APP_SECRET_KEY no es la que las cifró.",
        )
    if not devices:
        return [PLACEHOLDER_NODE]
    return [
        {
            "name": device["name"],
            "ip": device["address"],
            "ssh_port": device["port"],
            "model": device["model"],
            "username": device["username"],
            "password": device["password"],
        }
        for device in devices
    ]


# Tiempo mínimo entre intentos de failover de credencial por nodo. Oxidized
# reintenta los fallos seguido; sin este freno, un equipo caído dispararía una
# ronda de sondas SSH en cada ciclo.
_FAILOVER_COOLDOWN = 300.0
_ultimo_failover: dict[str, float] = {}


async def _failover_credencial(app, node: str) -> None:
    """Tras un respaldo fallido: prueba las demás credenciales del equipo y, si
    alguna conecta, la deja activa, recarga Oxidized y reintenta el respaldo.

    Si el equipo no tiene credenciales adicionales, o está caído (ninguna
    conecta), no hace nada: `ajustar_credencial_activa` solo cambia la activa
    cuando otra credencial autentica de verdad, así que no provoca aleteo."""
    ahora = time.monotonic()
    if ahora - _ultimo_failover.get(node, 0.0) < _FAILOVER_COOLDOWN:
        return
    _ultimo_failover[node] = ahora
    device = await app.state.devices.get_device_by_name(node)
    if not device or not device.get("extra_usernames"):
        return
    resultado = await ajustar_credencial_activa(app.state.devices, device["id"])
    if not resultado or not resultado["cambio"]:
        return
    settings = get_settings()
    try:
        await refresh_inventory(settings.oxidized_url)
        await trigger_node_backup(settings.oxidized_url, node)
    except (httpx.HTTPError, OSError):
        return


@router.post("/events", status_code=status.HTTP_204_NO_CONTENT)
async def oxidized_event(
    request: Request,
    payload: BackupEventIn,
    background: BackgroundTasks,
    x_oxidized_token: str = Header(default=""),
) -> None:
    _require_token(x_oxidized_token)
    if payload.node == PLACEHOLDER_NODE["name"]:
        return
    await request.app.state.backup_events.record_event(
        payload.node, payload.event, payload.commit
    )
    if payload.event == "node_fail":
        # En segundo plano: no bloquear la respuesta a Oxidized con sondas SSH.
        background.add_task(_failover_credencial, request.app, payload.node)
    if payload.event in ("node_success", "post_store"):
        settings = get_settings()
        try:
            config_text = await show_config(
                settings.oxidized_backup_repo, payload.node, "HEAD"
            )
        except (GitRepoError, NotFoundInRepoError, OSError):
            return
        meta = parse_routeros_metadata(config_text)
        if meta:
            await request.app.state.devices.update_metadata(payload.node, meta)
        await _revisar_integridad(request, payload.node, config_text)


@router.post(
    "/reload",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_admin)],
)
async def reload_oxidized() -> dict:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{settings.oxidized_url.rstrip('/')}/reload"
            )
            response.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Oxidized no aceptó la orden de recarga.",
        )
    return {"status": "reloaded"}
