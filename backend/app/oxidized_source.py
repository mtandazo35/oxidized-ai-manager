import secrets

from fastapi import APIRouter, Header, HTTPException, Request, status

from .config import get_settings
from .schemas import OxidizedNode


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


@router.get("/nodes", response_model=list[OxidizedNode])
async def oxidized_nodes(
    request: Request,
    x_oxidized_token: str = Header(default=""),
) -> list[dict]:
    settings = get_settings()
    if not secrets.compare_digest(x_oxidized_token, settings.oxidized_source_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing source token.",
        )
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
