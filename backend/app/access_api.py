"""Bitácora y control de acceso (solo administrador).

Es un registro de seguridad: dice quién entró, desde dónde y qué hizo. Se
reserva al administrador a propósito — a un cliente no le corresponde ver los
accesos de los demás, ni siquiera los suyos filtrados, porque la propia lista
de acciones revela la estructura interna de la plataforma.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .access import invalid_entries, parse_networks
from .auth import CurrentUser, client_ip, require_admin
from .schemas import AccessPolicy, ActivityEntry, IpBlockOut, AccountLockOut


router = APIRouter(
    prefix="/api/access",
    tags=["access"],
    dependencies=[Depends(require_admin)],
)


@router.get("/activity", response_model=list[ActivityEntry])
async def activity(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    action: str | None = Query(default=None, max_length=80),
    username: str | None = Query(default=None, max_length=128),
    ip: str | None = Query(default=None, max_length=64),
    only_failures: bool = Query(default=False),
) -> list[dict]:
    return await request.app.state.activity.list_entries(
        limit=limit,
        action=action or None,
        username=username or None,
        ip=ip or None,
        only_failures=only_failures,
    )


@router.get("/logins")
async def logins(request: Request) -> dict:
    """Desde qué IP entró cada cuenta por última vez."""
    return {"logins": await request.app.state.activity.recent_logins(limit=50)}


@router.get("/blocks")
async def blocks(request: Request) -> dict:
    access = request.app.state.access
    return {
        "ips": await access.list_ip_blocks(),
        "accounts": await access.list_account_locks(),
    }


@router.post("/unblock-ip", status_code=status.HTTP_204_NO_CONTENT)
async def unblock_ip(
    request: Request,
    ip: str = Query(max_length=64),
    actor: CurrentUser = Depends(require_admin),
) -> None:
    await request.app.state.access.clear_ip(ip)


@router.post("/unlock-account", status_code=status.HTTP_204_NO_CONTENT)
async def unlock_account(
    request: Request,
    username: str = Query(max_length=128),
    actor: CurrentUser = Depends(require_admin),
) -> None:
    await request.app.state.access.clear_account(username)


@router.get("/policy", response_model=AccessPolicy)
async def get_policy(request: Request) -> dict:
    values = await request.app.state.settings.get_all()
    return {
        "allowlist_enabled": values.get("login_allowlist_enabled") == "true",
        "allowlist": values.get("login_allowlist", ""),
    }


@router.put("/policy", response_model=AccessPolicy)
async def set_policy(
    request: Request,
    payload: AccessPolicy,
    actor: CurrentUser = Depends(require_admin),
) -> dict:
    texto = payload.allowlist.strip()
    malos = invalid_entries(texto)
    if malos:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"No se entienden estas entradas: {', '.join(malos[:5])}. "
            "Use direcciones (10.0.0.5) o redes (10.0.0.0/24).",
        )
    # Salvaguarda: activar la lista sin incluirse a uno mismo deja al operador
    # fuera en cuanto cierre sesión, y recuperarlo exige entrar por SSH.
    if payload.allowlist_enabled and parse_networks(texto):
        origen = client_ip(request)
        from .access import ip_in, is_never_blocked

        if origen and not ip_in(texto, origen) and not is_never_blocked(origen):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Su propia dirección ({origen}) no está en la lista: "
                "se quedaría fuera del panel. Añádala antes de activarla.",
            )
    await request.app.state.settings.set_many(
        {
            "login_allowlist": texto,
            "login_allowlist_enabled": "true" if payload.allowlist_enabled else "false",
        }
    )
    return {"allowlist_enabled": payload.allowlist_enabled, "allowlist": texto}
