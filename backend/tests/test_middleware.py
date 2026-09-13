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
