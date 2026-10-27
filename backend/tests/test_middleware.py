import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.middleware import SECURITY_HEADERS, SecurityHeadersMiddleware


pytestmark = pytest.mark.anyio


def client(base_url: str = "http://test") -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url=base_url)


async def login(api, username="admin", password="wrong"):
    return await api.post(
        "/api/auth/login", data={"username": username, "password": password}
    )


async def test_security_headers_are_present_on_every_response() -> None:
    async with client() as api:
        response = await api.get("/health/live")

    for header, value in SECURITY_HEADERS.items():
        assert response.headers[header] == value


async def test_security_headers_are_present_on_errors_too() -> None:
    async with client() as api:
        response = await api.get("/api/devices")

    assert response.status_code == 401
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


async def test_hsts_is_off_by_default() -> None:
    async with client("https://test") as api:
        response = await api.get("/health/live")

    assert "Strict-Transport-Security" not in response.headers


async def test_hsts_is_sent_only_over_https_when_enabled() -> None:
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    app = Starlette(routes=[Route("/", lambda request: PlainTextResponse("ok"))])
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=True)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="https://test") as api:
        secure = await api.get("/")
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        plain = await api.get("/")

    assert secure.headers["Strict-Transport-Security"].startswith("max-age=31536000")
    assert "Strict-Transport-Security" not in plain.headers


async def test_login_is_rate_limited_per_ip(user_repository) -> None:
    async with client() as api:
        codes = [(await login(api)).status_code for _ in range(6)]

    assert codes[:5] == [401] * 5
    assert codes[5] == 429


async def test_rate_limited_response_carries_retry_after(user_repository) -> None:
    async with client() as api:
        for _ in range(6):
            response = await login(api)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"


async def test_correct_password_still_works_below_the_limit(user_repository) -> None:
    async with client() as api:
        for _ in range(3):
            await login(api)
        good = await login(api, password="admin-test-password")

    assert good.status_code == 200


async def test_rate_limit_only_applies_to_the_login_endpoint() -> None:
    async with client() as api:
        codes = [(await api.get("/health/live")).status_code for _ in range(20)]

    assert codes == [200] * 20


# --- Orden de los middlewares, límite de cuerpo y CSP -----------------------


async def test_rate_limited_login_has_security_headers_and_is_logged(
    user_repository, activity_repository
) -> None:
    """Regresión: la bitácora iba por dentro del limitador y no veía el 429."""
    async with client() as api:
        for _ in range(6):
            response = await login(api)

    assert response.status_code == 429
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers
    assert "login.ratelimit" in activity_repository.actions()


async def test_unhandled_error_still_carries_security_headers() -> None:
    from starlette.applications import Starlette
    from starlette.routing import Route

    async def revienta(request):
        raise RuntimeError("fallo")

    app = Starlette(routes=[Route("/", revienta)])
    app.add_middleware(SecurityHeadersMiddleware)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.get("/")

    assert response.status_code == 500
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in response.headers


async def test_body_over_limit_is_rejected_by_content_length(auth_headers) -> None:
    from app.middleware import MAX_BODY_BYTES

    async with client() as api:
        response = await api.post(
            "/api/devices",
            headers={**auth_headers, "Content-Type": "application/json"},
            content=b"x" * (MAX_BODY_BYTES + 1),
        )

    assert response.status_code == 413
    assert "demasiado grande" in response.json()["detail"]
    assert response.headers["X-Frame-Options"] == "DENY"


async def test_streamed_body_over_limit_is_rejected(auth_headers, monkeypatch) -> None:
    """Sin Content-Length (por trozos) también se corta."""
    monkeypatch.setattr("app.middleware.MAX_BODY_BYTES", 1024)

    async def trozos():
        for _ in range(10):
            yield b"y" * 512

    async with client() as api:
        response = await api.post(
            "/api/devices",
            headers={**auth_headers, "Content-Type": "application/json"},
            content=trozos(),
        )

    assert response.status_code == 413


async def test_body_under_limit_passes(auth_headers) -> None:
    async with client() as api:
        response = await api.post(
            "/api/devices",
            headers=auth_headers,
            json={"name": "rb-limite", "address": "192.0.2.10"},
        )

    assert response.status_code == 201


async def test_csp_allows_panel_scripts_by_hash_only() -> None:
    import base64
    import hashlib
    import re

    from app.middleware import PANEL_FILE

    async with client() as api:
        response = await api.get("/")

    csp = response.headers["Content-Security-Policy"]
    script_src = next(
        part.strip() for part in csp.split(";") if part.strip().startswith("script-src")
    )
    assert "'unsafe-inline'" not in script_src

    html = PANEL_FILE.read_text(encoding="utf-8")
    bloques = re.findall(r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", html, re.S)
    assert bloques, "el panel debería tener scripts en línea"
    esperados = {
        "'sha256-"
        + base64.b64encode(hashlib.sha256(b.encode("utf-8")).digest()).decode()
        + "'"
        for b in bloques
    }
    declarados = set(script_src.split()[1:]) - {"'self'"}
    assert declarados == esperados


async def test_csp_follows_panel_edits_without_restart(tmp_path, monkeypatch) -> None:
    import os

    import app.middleware as mw

    panel = tmp_path / "index.html"
    panel.write_text("<script>uno()</script>", encoding="utf-8")
    monkeypatch.setattr(mw, "PANEL_FILE", panel)
    monkeypatch.setattr(mw, "_csp_cache", None)
    antes = mw.content_security_policy()

    panel.write_text("<script>dos()</script>", encoding="utf-8")
    stat = panel.stat()
    os.utime(panel, (stat.st_atime, stat.st_mtime + 5))
    despues = mw.content_security_policy()

    assert antes != despues
    assert mw.inline_script_hashes("<script>dos()</script>")[0] in despues
