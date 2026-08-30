import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module


pytestmark = pytest.mark.anyio


def client(auth_headers: dict[str, str]) -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers
    )


async def do_import(api: AsyncClient, text: str):
    return await api.post("/api/devices/import", json={"text": text})


async def test_import_requires_login() -> None:
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.post("/api/devices/import", json={"text": "a,b"})

    assert response.status_code == 401


async def test_import_mixed_lines(auth_headers) -> None:
    text = "\n".join(
        [
            "nombre,ip,puerto,usuario,clave",
            "rb-core-01,192.0.2.10,2222,backup,S3cret",
            "",
            "# comentario",
            "rb-core-02;192.0.2.20",
            "rb-core-03\t192.0.2.30\t9922",
            "malo sin coma",
            "rb-core-04,192.0.2.40,abc",
            "../etc,192.0.2.50",
        ]
    )
    async with client(auth_headers) as api:
        response = await do_import(api, text)
        listed = await api.get("/api/devices")

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 3
    assert body["duplicates"] == []
    assert len(body["errors"]) == 3
    assert [e["line"] for e in body["errors"]] == [7, 8, 9]

    devices = {d["name"]: d for d in listed.json()}
    assert devices["rb-core-01"]["port"] == 2222
    assert devices["rb-core-01"]["username"] == "backup"
    assert devices["rb-core-02"]["port"] == 22
    assert devices["rb-core-03"]["port"] == 9922


async def test_import_with_group_column(auth_headers) -> None:
    async with client(auth_headers) as api:
        await do_import(api, "rb-emp-01,192.0.2.60,22,backup,Clave,EmpresaX")
        listed = await api.get("/api/devices")

    devices = {d["name"]: d for d in listed.json()}
    assert devices["rb-emp-01"]["group_name"] == "EmpresaX"


async def test_import_reports_duplicates(auth_headers) -> None:
    async with client(auth_headers) as api:
        first = await do_import(api, "rb-core-01,192.0.2.10")
        second = await do_import(api, "rb-core-01,192.0.2.99")

    assert first.json()["created"] == 1
    assert second.json()["created"] == 0
    assert second.json()["duplicates"] == ["rb-core-01"]


async def test_import_passwords_not_leaked(auth_headers) -> None:
    async with client(auth_headers) as api:
        await do_import(api, "rb-core-01,192.0.2.10,22,backup,S3cret")
        listed = await api.get("/api/devices")

    assert "S3cret" not in listed.text
