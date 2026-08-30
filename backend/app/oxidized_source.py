import secrets

from fastapi import APIRouter, Header, HTTPException, Request, status

from .config import get_settings
from .schemas import BackupEventIn, OxidizedNode


router = APIRouter(prefix="/api/oxidized", tags=["oxidized"])

# Oxidized aborts when the source yields zero nodes, so an empty inventory
# still returns the inert phase-1 placeholder (interval 0, no credentials).
PLACEHOLDER_NODE = {
    "name": "phase1-placeholder",
    "ip": "127.0.0.1",
    "model": "routeros",
    "username": "",
    "password": "",
}


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
