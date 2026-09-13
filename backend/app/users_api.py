"""Gestión de cuentas (solo administrador).

El modelo es simple a propósito: cada cuenta tiene un **rol** y pertenece a una
**empresa** (`group_name`), la misma con la que se agrupan los equipos. Un
cliente recibe una cuenta de su empresa y solo ve sus propios respaldos; el
filtrado se aplica en cada endpoint, no en el panel.

Roles:

- `admin`    — todo, incluidas cuentas y ajustes globales; ve todas las empresas.
- `operador` — alta, edición y respaldos de **su** empresa.
- `auditor`  — solo lectura de su empresa, con acceso a la auditoría.
- `lector`   — solo lectura de su empresa, sin auditoría.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .auth import CurrentUser, require_admin
from .repository import DuplicateUserError
from .schemas import ROLES, UserCreate, UserOut, UserUpdate
from .security import hash_password


router = APIRouter(
    prefix="/api/users",
    tags=["users"],
    dependencies=[Depends(require_admin)],
)


def _require_group(role: str, group_name: str) -> str:
    """Una cuenta no administradora sin empresa no vería nada: se rechaza.

    Es preferible fallar aquí, al crearla, que entregar al cliente un panel
    vacío sin explicación.
    """
    if role == "admin":
        return ""
    if not group_name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Indique la empresa: sin ella la cuenta no vería ningún equipo.",
        )
    return group_name.strip()


# Cada permiso dice de qué depende. Los tres primeros criterios se consultan a
# los predicados reales de CurrentUser, así que la tabla no puede desviarse de
# lo que la API hace de verdad: si mañana `can_write` cambia, esto cambia solo.
CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("Ver equipos, respaldos y diferencias", "todos"),
    ("Descargar las configuraciones guardadas", "todos"),
    ("Ver la auditoría de configuraciones", "auditar"),
    ("Dar de alta, editar y borrar equipos", "escribir"),
    ("Lanzar respaldos (manual y masivo)", "escribir"),
    ("Carga masiva de equipos (CSV / Excel)", "escribir"),
    ("Ver equipos de TODAS las empresas", "admin"),
    ("Ajustes globales (intervalo, Git remoto)", "admin"),
    ("Crear y gestionar cuentas", "admin"),
    ("Bitácora y control de acceso por IP", "admin"),
    ("Actualizar la plataforma", "admin"),
)

ROLE_DESCRIPTIONS = {
    "admin": "Administrador. Todas las empresas y toda la operación.",
    "operador": "Opera su empresa: inventario y respaldos.",
    "auditor": "Solo lectura de su empresa, con acceso a la auditoría.",
    "lector": "Solo lectura de su empresa.",
}


def _grants(role: str, criterion: str) -> bool:
    usuario = CurrentUser(username="", role=role, group_name="")
    if criterion == "todos":
        return True
    if criterion == "auditar":
        return usuario.can_audit()
    if criterion == "escribir":
        return usuario.can_write()
    return usuario.is_admin


@router.get("/roles")
async def roles() -> dict:
    """Qué puede hacer cada rol. Se calcula, no se escribe a mano."""
    return {
        "roles": [
            {
                "name": role,
                "description": ROLE_DESCRIPTIONS.get(role, ""),
                "scope": (
                    "todas las empresas" if role == "admin" else "solo su empresa"
                ),
            }
            for role in ROLES
        ],
        "capabilities": [
            {
                "label": label,
                "grants": {role: _grants(role, criterion) for role in ROLES},
            }
            for label, criterion in CAPABILITIES
        ],
    }


@router.get("", response_model=list[UserOut])
async def list_users(request: Request) -> list[dict]:
    return await request.app.state.users.list_users()


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(request: Request, payload: UserCreate) -> dict:
    group_name = _require_group(payload.role, payload.group_name)
    try:
        return await request.app.state.users.create_user(
            payload.username,
            hash_password(payload.password),
            must_change_password=payload.must_change_password,
            role=payload.role,
            group_name=group_name,
        )
    except DuplicateUserError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe una cuenta con ese usuario.",
        )


@router.patch("/{username}", response_model=UserOut)
async def update_user(
    request: Request,
    username: str,
    payload: UserUpdate,
    actor: CurrentUser = Depends(require_admin),
) -> dict:
    users = request.app.state.users
    stored = await users.get_by_username(username)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada."
        )

    new_role = payload.role or stored.get("role", "lector")

    # Esta comprobación va primero: degradar al último administrador se rechaza
    # pase lo que pase, y si se validara antes la empresa el error sería un 422
    # pidiendo una empresa que no viene al caso.
    if (
        stored.get("role") == "admin"
        and new_role != "admin"
        and await users.count_admins() <= 1
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Es el único administrador: cree otro antes de cambiarle el rol.",
        )

    data: dict = {}
    if payload.role is not None:
        data["role"] = payload.role
    if payload.group_name is not None or payload.role is not None:
        group = (
            payload.group_name
            if payload.group_name is not None
            else stored.get("group_name", "")
        )
        data["group_name"] = _require_group(new_role, group)
    if payload.password is not None:
        data["password_hash"] = hash_password(payload.password)
        data["must_change_password"] = True

    updated = await users.update_user(username, data)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada."
        )
    return updated


@router.delete("/{username}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    request: Request, username: str, actor: CurrentUser = Depends(require_admin)
) -> None:
    users = request.app.state.users
    if username == actor.username:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No puede borrar su propia cuenta.",
        )
    stored = await users.get_by_username(username)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada."
        )
    if stored.get("role") == "admin" and await users.count_admins() <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Es el único administrador: no se puede borrar.",
        )
    await users.delete_user(username)
