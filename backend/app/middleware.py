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

import base64
import hashlib
import json
import logging
import re
import time
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import get_settings
from .security import decode_access_token

log = logging.getLogger("oxidized-ai-manager.actividad")

log_errores = logging.getLogger("oxidized-ai-manager.errores")

# El panel es una SPA de un solo archivo con sus scripts en línea. La CSP no
# permite `'unsafe-inline'` para scripts: autoriza cada bloque <script> por su
# hash SHA-256, así que un script inyectado (o un atributo `onclick=`) no se
# ejecuta aunque llegue al DOM. Los estilos sí llevan `'unsafe-inline'`: el
# panel usa atributos `style=` y un estilo inyectado no ejecuta código. No se
# carga nada de terceros: ni CDN, ni fuentes externas.
PANEL_FILE = Path(__file__).parent / "static" / "index.html"

CSP_TEMPLATE = (
    "default-src 'self'; "
    "script-src 'self'{hashes}; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'"
)

# Bloques <script> sin `src`: su contenido exacto es lo que el navegador hashea.
_INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script\s*>", re.DOTALL | re.IGNORECASE
)

# (mtime, política). Se recalcula cuando el archivo cambia: en desarrollo se
# edita el panel sin reiniciar el backend y la CSP tiene que seguirle.
_csp_cache: tuple[float, str] | None = None


def inline_script_hashes(html: str) -> list[str]:
    return [
        "'sha256-"
        + base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
        + "'"
        for body in _INLINE_SCRIPT.findall(html)
    ]


def content_security_policy() -> str:
    global _csp_cache
    try:
        mtime = PANEL_FILE.stat().st_mtime
    except OSError:
        return CSP_TEMPLATE.format(hashes="")
    if _csp_cache is None or _csp_cache[0] != mtime:
        try:
            html = PANEL_FILE.read_text(encoding="utf-8")
        except OSError:
            return CSP_TEMPLATE.format(hashes="")
        hashes = "".join(f" {h}" for h in dict.fromkeys(inline_script_hashes(html)))
        _csp_cache = (mtime, CSP_TEMPLATE.format(hashes=hashes))
    return _csp_cache[1]


SECURITY_HEADERS = {
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
        try:
            response = await call_next(request)
        except Exception:
            # Sin esto el 500 lo fabrica Starlette por fuera de este
            # middleware y sale sin cabeceras de seguridad.
            log_errores.exception("Error no controlado en %s", request.url.path)
            response = JSONResponse(
                status_code=500, content={"detail": "Error interno del servidor."}
            )
        response.headers.setdefault(
            "Content-Security-Policy", content_security_policy()
        )
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
            # Lo lee la bitácora, que envuelve a este middleware: el endpoint
            # de login no llega a ejecutarse y no lo anota él.
            request.state.rate_limited = True
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
    ("POST", r"^/api/auth/logout$", "logout"),
)

# El login se registra por su cuenta, con el motivo del fallo; y las llamadas
# de Oxidized llevan su propio token de servicio, no una sesión.
SKIP_PATHS = ("/api/auth/login", "/api/oxidized/events", "/api/backups/check")

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
        if getattr(request.state, "rate_limited", False):
            await self._record_rate_limited(request)
            return response
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

    @staticmethod
    async def _record_rate_limited(request: Request) -> None:
        """Un login frenado por el limitador también es un intento: se anota.

        No se lee el formulario (el nombre de usuario): el limitador cortó la
        petición antes y leer el cuerpo aquí sería procesar justo lo que se
        quería rechazar.
        """
        repository = getattr(request.app.state, "activity", None)
        if repository is None:
            return
        try:
            await repository.record(
                action="login.ratelimit",
                ip=request.client.host if request.client else "",
                detail="demasiados intentos por minuto desde esta IP",
                ok=False,
            )
        except Exception:
            log.warning("No se pudo anotar la acción en la bitácora", exc_info=True)


# --- Límite de tamaño del cuerpo --------------------------------------------

MAX_BODY_BYTES = 8 * 1024 * 1024


class _CuerpoDemasiadoGrande(Exception):
    pass


class BodySizeLimitMiddleware:
    """Rechaza con 413 los cuerpos de más de `MAX_BODY_BYTES`.

    Antes este límite solo existía en Nginx Proxy Manager. Se mira primero
    `Content-Length`; si no viene (cuerpo por trozos), se cuentan los bytes a
    medida que llegan y se corta en cuanto se pasa. Es ASGI puro para poder
    contar el cuerpo sin leerlo entero en memoria.
    """

    def __init__(self, app: ASGIApp, max_bytes: int | None = None) -> None:
        self.app = app
        self._max = max_bytes

    @property
    def max_bytes(self) -> int:
        # Se lee en cada petición para que una prueba pueda bajarlo.
        return self._max if self._max is not None else MAX_BODY_BYTES

    @staticmethod
    async def _too_large(send: Send) -> None:
        cuerpo = json.dumps(
            {"detail": "La petición es demasiado grande (máximo 8 MB)."},
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(cuerpo)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": cuerpo})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limite = self.max_bytes
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declarado = int(value)
                except ValueError:
                    declarado = 0
                if declarado > limite:
                    await self._too_large(send)
                    return

        recibido = 0
        excedido = False
        respuesta_iniciada = False

        async def contar() -> Message:
            nonlocal recibido, excedido
            message = await receive()
            if message["type"] == "http.request":
                recibido += len(message.get("body", b""))
                if recibido > limite:
                    excedido = True
                    raise _CuerpoDemasiadoGrande()
            return message

        async def enviar(message: Message) -> None:
            nonlocal respuesta_iniciada
            # Pasado el límite, lo que intente responder la aplicación (un 400
            # por cuerpo ilegible, por ejemplo) se descarta: sale el 413.
            if excedido:
                return
            if message["type"] == "http.response.start":
                respuesta_iniciada = True
            await send(message)

        try:
            await self.app(scope, contar, enviar)
        except _CuerpoDemasiadoGrande:
            pass
        if excedido and not respuesta_iniciada:
            await self._too_large(send)
