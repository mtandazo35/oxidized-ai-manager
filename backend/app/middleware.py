"""Protecciones que antes ponía el Nginx propio del proyecto.

El despliegue expone el panel detrás de un **Nginx Proxy Manager externo**, que
no forma parte de este repositorio. NPM resuelve TLS, HTTP/2 y listas de
acceso, pero no aplica cabeceras de seguridad por ruta ni rate-limit por
endpoint sin configuración manual en cada proxy host. Ese tipo de protección se
pierde en cuanto alguien recrea el proxy host, así que vive aquí: viaja con la
aplicación.

Sobre la IP del cliente: `request.client.host` ya viene reescrita por el
`ProxyHeadersMiddleware` de uvicorn a partir de `X-Forwarded-For`, pero **solo**
cuando el par TCP está en `FORWARDED_ALLOW_IPS`. Por eso aquí no se vuelve a
interpretar ninguna cabecera `X-Forwarded-*`: duplicar esa lógica sería
duplicar la decisión de en quién confiar.
"""

import logging
import re
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import get_settings
from .security import decode_access_token

log = logging.getLogger("oxidized-ai-manager.actividad")

# El panel es una SPA de un solo archivo con estilos y scripts en línea, de ahí
# los `unsafe-inline`. No carga nada de terceros: ni CDN, ni fuentes externas.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

HSTS_HEADER = "max-age=31536000; includeSubDomains"

LOGIN_PATH = "/api/auth/login"
LOGIN_RATE_LIMIT = 5
LOGIN_RATE_WINDOW_SECONDS = 60

# Estado a nivel de módulo, igual que el bloqueo por cuenta de `auth.py`: así
# las pruebas pueden reiniciarlo entre casos y ajustar el límite sin recrear la
# aplicación.
_login_hits: dict[str, list[float]] = {}


def reset_login_rate_limit() -> None:
    _login_hits.clear()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Cabeceras de seguridad en cada respuesta.

    `hsts` queda desactivado por defecto: anunciarlo desde un despliegue que
    todavía se alcanza por HTTP (o con certificado autofirmado) deja al
    navegador clavado en HTTPS para ese host. Actívelo (`APP_ENABLE_HSTS=true`)
    cuando el dominio y su certificado estén confirmados.
    """

    def __init__(self, app, enable_hsts: bool = False) -> None:
        super().__init__(app)
        self._enable_hsts = enable_hsts

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if self._enable_hsts and request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", HSTS_HEADER)
        return response


class LoginRateLimitMiddleware(BaseHTTPMiddleware):
    """Límite de intentos de login por IP de origen.

    Complementa el bloqueo por cuenta de `auth.py`: aquel frena la fuerza bruta
    contra *un* usuario, este frena a un origen que rota nombres de usuario.
    Es en memoria y por proceso, igual que el bloqueo por cuenta; con una sola
    instancia del backend alcanza. Si algún día hay varias, esto se mueve a
    Redis (que ya está en el stack).
    """

    def __init__(
        self,
        app,
        limit: int | None = None,
        window_seconds: int | None = None,
    ) -> None:
        super().__init__(app)
        self._limit = limit
        self._window = window_seconds

    @property
    def _window_seconds(self) -> int:
        # Se lee en cada petición para que ajustarlo (o relajarlo en una
        # prueba) no exija recrear la aplicación.
        return self._window if self._window is not None else LOGIN_RATE_WINDOW_SECONDS

    def _too_many(self, client: str, now: float) -> bool:
        limit = self._limit if self._limit is not None else LOGIN_RATE_LIMIT
        window = self._window_seconds
        recent = [stamp for stamp in _login_hits.get(client, []) if now - stamp < window]
        recent.append(now)
        _login_hits[client] = recent
        if len(_login_hits) > 10000:
            # Poda defensiva: evita que un barrido de IPs falsificadas haga
            # crecer el diccionario sin límite.
            for key in [
                key
                for key, stamps in _login_hits.items()
                if not stamps or now - stamps[-1] >= window
            ]:
                del _login_hits[key]
        return len(recent) > limit

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path != LOGIN_PATH or request.method != "POST":
            return await call_next(request)
        client = request.client.host if request.client else "unknown"
        if self._too_many(client, time.monotonic()):
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Demasiados intentos de ingreso. Espere un minuto."
                },
                headers={"Retry-After": str(self._window_seconds)},
            )
        return await call_next(request)


# --- Bitácora de acciones -------------------------------------------------

# Traducción de (método, ruta) a un nombre legible. Se hace aquí y no en cada
# endpoint para que ninguna acción nueva se quede sin registrar por olvido: lo
# que no encaje en la tabla se anota igualmente con su método y ruta.
ACTION_MAP: tuple[tuple[str, str, str], ...] = (
    ("POST", r"^/api/devices$", "equipo.alta"),
    ("PATCH", r"^/api/devices/\d+$", "equipo.edicion"),
    ("DELETE", r"^/api/devices/\d+$", "equipo.baja"),
    ("POST", r"^/api/devices/import$", "equipo.carga-masiva"),
    ("POST", r"^/api/devices/\d+/backup$", "respaldo.manual"),
    ("POST", r"^/api/backups/run$", "respaldo.masivo"),
    ("PUT", r"^/api/settings$", "ajustes.cambio"),
    ("POST", r"^/api/users$", "cuenta.alta"),
    ("PATCH", r"^/api/users/[^/]+$", "cuenta.edicion"),
    ("DELETE", r"^/api/users/[^/]+$", "cuenta.baja"),
    ("POST", r"^/api/oxidized/reload$", "oxidized.recarga"),
    ("POST", r"^/api/system/update$", "sistema.actualizacion"),
    ("POST", r"^/api/access/unblock-ip$", "acceso.desbloqueo-ip"),
    ("POST", r"^/api/access/unlock-account$", "acceso.desbloqueo-cuenta"),
)

# El login se registra por su cuenta, con el motivo del fallo; y las llamadas
# de Oxidized llevan su propio token de servicio, no una sesión.
SKIP_PATHS = ("/api/auth/login", "/api/oxidized/events")

MUTATING = ("POST", "PUT", "PATCH", "DELETE")


def describe_action(method: str, path: str) -> str:
    for verbo, patron, nombre in ACTION_MAP:
        if verbo == method and re.match(patron, path):
            return nombre
    return f"{method.lower()} {path}"[:80]


class ActivityLogMiddleware(BaseHTTPMiddleware):
    """Anota en la bitácora toda petición que cambia algo.

    Nunca debe tumbar la petición que registra: si la base no está disponible,
    se pierde la anotación, no la operación.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        if request.method not in MUTATING or request.url.path in SKIP_PATHS:
            return response
        repository = getattr(request.app.state, "activity", None)
        if repository is None:
            return response
        token = request.headers.get("authorization", "")
        username = ""
        if token.lower().startswith("bearer "):
            username = (
                decode_access_token(token[7:], get_settings().app_secret_key) or ""
            )
        try:
            await repository.record(
                action=describe_action(request.method, request.url.path),
                username=username,
                ip=request.client.host if request.client else "",
                target=request.url.path[:190],
                detail=f"HTTP {response.status_code}",
                ok=response.status_code < 400,
            )
        except Exception:
            log.warning("No se pudo anotar la acción en la bitácora", exc_info=True)
        return response
