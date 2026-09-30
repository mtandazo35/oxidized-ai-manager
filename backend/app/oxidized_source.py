import secrets

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from .auth import require_admin
from .config import get_settings
from .gitrepo import GitRepoError, NotFoundInRepoError, list_versions, show_config
from .integrity import revisar
from .metadata import parse_routeros_metadata
from .schemas import BackupEventIn, OxidizedNode


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
        versiones = await list_versions(repo, node, 2)
        anterior = None
        if len(versiones) > 1:
            anterior = await show_config(repo, node, versiones[1]["commit"])
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
    if not secrets.compare_digest(x_oxidized_token, settings.oxidized_source_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing source token.",
        )


@router.get("/nodes", response_model=list[OxidizedNode])
async def oxidized_nodes(
    request: Request,
    x_oxidized_token: str = Header(default=""),
) -> list[dict]:
    _require_token(x_oxidized_token)
    devices = await request.app.state.devices.list_oxidized_nodes()
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


@router.post("/events", status_code=status.HTTP_204_NO_CONTENT)
async def oxidized_event(
    request: Request,
    payload: BackupEventIn,
    x_oxidized_token: str = Header(default=""),
) -> None:
    _require_token(x_oxidized_token)
    if payload.node == PLACEHOLDER_NODE["name"]:
        return
    await request.app.state.backup_events.record_event(
        payload.node, payload.event, payload.commit
    )
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
            detail="Oxidized did not accept the reload request.",
        )
    return {"status": "reloaded"}
