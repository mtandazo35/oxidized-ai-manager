from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

from .access import NEVER_BLOCK, ip_in, is_allowed, is_never_blocked
from .config import get_settings
from .schemas import ChangePasswordRequest, TokenResponse
from .security import (
    burn_password_check,
    create_access_token,
    decode_access_claims,
    hash_password,
    verify_password,
)


router = APIRouter(prefix="/api/auth", tags=["auth"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Límite de la tabla `account_locks` y del desbloqueo manual: un nombre más
# largo no existe como cuenta y, si se anotara, no se podría desbloquear.
MAX_USERNAME_LENGTH = 128

# Lo único que puede hacer una cuenta con la clave pendiente de cambio. El
# panel ya obliga a cambiarla, pero la restricción tiene que vivir aquí: con
# el token en la mano, la API no debe servir nada más hasta el cambio.
PASSWORD_CHANGE_PATHS = frozenset(
    {
        "/api/auth/me",
        "/api/auth/change-password",
        "/api/auth/logout",
        "/api/auth/refresh",
    }
)


def client_ip(request: Request) -> str:
    """IP de origen ya resuelta por uvicorn a partir de X-Forwarded-For.

    Solo se fía de esa cabecera si el par TCP está en FORWARDED_ALLOW_IPS, así
    que aquí no hay que volver a decidir en quién confiar.
    """
    return request.client.host if request.client else ""


@dataclass(frozen=True)
class CurrentUser:
    """Quién pide y con qué alcance.

    El rol y la empresa se leen de la base en cada petición, no del token: así
    un cambio de rol o de empresa aplica al instante y no espera a que caduque
    el JWT (8 h).
    """

    username: str
    role: str
    group_name: str
    token_version: int = 0

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def scope(self) -> str | None:
        """Empresa por la que filtrar; `None` significa "todas".

        Solo el administrador obtiene `None`. Una cuenta no administradora sin
        empresa asignada recibe `""`, que **no coincide con ningún equipo** de
        un inventario con empresas: falla cerrada a propósito.
        """
        return None if self.is_admin else self.group_name

    def can_write(self) -> bool:
        return self.role in ("admin", "operador")

    def can_audit(self) -> bool:
        return self.role in ("admin", "operador", "auditor")


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sesión no válida o caducada.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_user(
    request: Request, token: str = Depends(oauth2_scheme)
) -> CurrentUser:
    settings = get_settings()
    state = request.app.state
    claims = decode_access_claims(token, settings.app_secret_key)
    if claims is None:
        raise _unauthorized()
    user = await state.users.get_by_username(claims.username)
    # Un token de una versión anterior quedó revocado (cambio de clave,
    # restablecimiento o cierre de sesión), aunque su firma y su fecha valgan.
    if user is None or claims.version != int(user.get("token_version") or 0):
        raise _unauthorized()

    # La lista de IPs permitidas vale para toda la API, no solo para el login:
    # si no, un token robado seguiría sirviendo desde cualquier parte.
    values = await state.settings.get_all()
    if not is_allowed(
        values.get("login_allowlist", ""),
        values.get("login_allowlist_enabled") == "true",
        client_ip(request),
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Su dirección no está autorizada para acceder a este panel.",
        )

    if user.get("must_change_password") and (
        request.url.path not in PASSWORD_CHANGE_PATHS
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Debe cambiar su clave antes de continuar.",
        )

    return CurrentUser(
        username=claims.username,
        role=user.get("role") or "lector",
        group_name=user.get("group_name") or "",
        token_version=claims.version,
    )


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


async def require_write(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """Alta, edición, borrado y disparo de respaldos."""
    if not user.can_write():
        raise _forbidden("Su cuenta es de solo lectura.")
    return user


async def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """Ajustes globales, usuarios y recarga de Oxidized."""
    if not user.is_admin:
        raise _forbidden("Solo un administrador puede hacer esto.")
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request, form: OAuth2PasswordRequestForm = Depends()
) -> dict:
    """Tres filtros antes de mirar siquiera la contraseña: la IP debe estar
    permitida, no puede estar bloqueada, y la cuenta tampoco."""
    settings = get_settings()
    state = request.app.state
    ip = client_ip(request)
    values = await state.settings.get_all()
    allowlist = values.get("login_allowlist", "")

    allowlist_enabled = values.get("login_allowlist_enabled") == "true"
    # Origen de confianza: la red de gestión (NEVER_BLOCK) o, con la lista
    # activada, una red que el operador declaró permitida. Desde ahí el
    # bloqueo por cuenta no aplica: si no, un atacante externo podría dejar
    # fuera al administrador fallando su clave a propósito.
    origen_confiable = bool(ip) and (
        ip_in(" ".join(NEVER_BLOCK), ip)
        or (allowlist_enabled and ip_in(allowlist, ip))
    )

    async def anotar(action: str, detail: str = "", ok: bool = False) -> None:
        await state.activity.record(
            action=action, username=form.username, ip=ip, detail=detail, ok=ok
        )

    # 1. Lista de IPs permitidas.
    if not is_allowed(allowlist, allowlist_enabled, ip):
        await anotar("login.denied", "IP fuera de la lista de permitidas")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Su dirección no está autorizada para acceder a este panel.",
        )

    # 2. IP bloqueada por acumular fallos.
    if await state.access.ip_block(ip):
        await anotar("login.blocked", "IP bloqueada por intentos fallidos")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Su dirección está bloqueada temporalmente por intentos fallidos.",
        )

    # 3. Cuenta bloqueada (salvo desde un origen de confianza).
    if not origen_confiable and await state.access.account_lock(form.username):
        await anotar("login.locked", "cuenta bloqueada por intentos fallidos")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Cuenta bloqueada temporalmente por intentos fallidos. Espere unos minutos.",
        )

    nombre_valido = len(form.username) <= MAX_USERNAME_LENGTH
    user = (
        await state.users.get_by_username(form.username) if nombre_valido else None
    )
    if user is None:
        # Mismo coste que comprobar una clave real: el tiempo de respuesta no
        # debe revelar si la cuenta existe.
        burn_password_check(form.password)
        clave_ok = False
    else:
        clave_ok = verify_password(form.password, user["password_hash"])
    if not clave_ok:
        cuenta_bloqueada = False
        if nombre_valido:
            cuenta_bloqueada = await state.access.register_account_failure(
                form.username,
                settings.login_failure_window_minutes,
                settings.account_lock_threshold,
                settings.account_lock_minutes,
            )
        ip_bloqueada = False
        # Una IP de confianza nunca se autobloquea: si no, cualquiera podría
        # dejar fuera al operador atacando desde su propia red de gestión.
        if ip and not is_never_blocked(ip, allowlist):
            ip_bloqueada = await state.access.register_ip_failure(
                ip,
                settings.login_failure_window_minutes,
                settings.ip_block_threshold,
                settings.ip_block_minutes,
            )
        detalle = "usuario o clave incorrectos"
        if cuenta_bloqueada:
            detalle += "; cuenta bloqueada"
        if ip_bloqueada:
            detalle += "; IP bloqueada"
        await anotar("login.fail", detalle)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o clave incorrectos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await state.access.clear_account(form.username)
    await anotar("login.ok", f"rol {user.get('role', '?')}", ok=True)
    token = create_access_token(
        user["username"],
        settings.app_secret_key,
        settings.access_token_ttl_minutes,
        int(user.get("token_version") or 0),
    )
    return {"access_token": token, "token_type": "bearer"}


@router.post("/refresh", response_model=TokenResponse)
async def refresh(user: CurrentUser = Depends(current_user)) -> dict:
    """Alarga la sesión mientras haya actividad.

    El panel lo llama cuando el usuario está usando la aplicación y al token
    le queda menos de la mitad de vida. Si nadie toca nada, nadie renueva y el
    token caduca solo: la caducidad la impone el servidor, no el navegador.
    """
    settings = get_settings()
    token = create_access_token(
        user.username,
        settings.app_secret_key,
        settings.access_token_ttl_minutes,
        user.token_version,
    )
    return {"access_token": token, "token_type": "bearer"}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, user: CurrentUser = Depends(current_user)) -> None:
    """Cierra **todas** las sesiones de la cuenta.

    El JWT no tiene estado: la única forma de invalidarlo antes de que caduque
    es subir la versión de sesión de la cuenta, y eso afecta a todos los
    tokens emitidos hasta ahora, no solo al de este navegador.
    """
    # La bitácora la escribe el middleware (acción `logout`).
    await request.app.state.users.bump_token_version(user.username)


@router.get("/me")
async def me(request: Request, user: CurrentUser = Depends(current_user)) -> dict:
    settings = get_settings()
    stored = await request.app.state.users.get_by_username(user.username)
    return {
        "username": user.username,
        "idle_minutes": settings.session_idle_minutes,
        "role": user.role,
        "group_name": user.group_name,
        "can_write": user.can_write(),
        "can_audit": user.can_audit(),
        "is_admin": user.is_admin,
        "must_change_password": bool(stored and stored.get("must_change_password")),
    }


@router.post("/change-password", response_model=TokenResponse)
async def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    user: CurrentUser = Depends(current_user),
) -> dict:
    """Cambia la clave y revoca las demás sesiones de la cuenta.

    Cambiar la clave sube la versión de sesión, así que el token con el que se
    hizo la petición deja de valer. Se devuelve uno nuevo para que el panel
    siga dentro sin pedir la clave otra vez.
    """
    stored = await request.app.state.users.get_by_username(user.username)
    if stored is None or not verify_password(
        payload.current_password, stored["password_hash"]
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La clave actual no es correcta.",
        )
    await request.app.state.users.update_password(
        user.username, hash_password(payload.new_password)
    )
    await request.app.state.activity.record(
        action="password.change",
        username=user.username,
        ip=client_ip(request),
        ok=True,
    )
    actualizado = await request.app.state.users.get_by_username(user.username)
    settings = get_settings()
    token = create_access_token(
        user.username,
        settings.app_secret_key,
        settings.access_token_ttl_minutes,
        int((actualizado or {}).get("token_version") or 0),
    )
    return {"access_token": token, "token_type": "bearer"}
