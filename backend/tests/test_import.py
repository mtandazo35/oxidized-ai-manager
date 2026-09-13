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
    # "../etc" ya no se rechaza: se normaliza a "etc". Sigue siendo seguro
    # porque el nombre resultante nunca puede llevar barras ni empezar por
    # punto, que es lo que protege al repositorio de respaldos.
    assert body["created"] == 4
    assert body["duplicates"] == []
    assert len(body["errors"]) == 2
    assert [e["line"] for e in body["errors"]] == [7, 8]

    devices = {d["name"]: d for d in listed.json()}
    assert devices["rb-core-01"]["port"] == 2222
    assert devices["rb-core-01"]["username"] == "backup"
    assert devices["rb-core-02"]["port"] == 22
    assert devices["rb-core-03"]["port"] == 9922
    assert "etc" in devices
    assert all("/" not in nombre and ".." not in nombre for nombre in devices)


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


async def test_import_accepts_the_platform_column(
    auth_headers, device_repository
) -> None:
    async with client(auth_headers) as api:
        response = await api.post(
            "/api/devices/import",
            headers=auth_headers,
            json={
                "text": (
                    "olt-norte,192.0.2.40,22,admin,Clave,EmpresaA,smartax\n"
                    "rb-sur,192.0.2.41,22,backup,Clave,EmpresaA,"
                )
            },
        )
        listed = await api.get("/api/devices", headers=auth_headers)

    assert response.json()["created"] == 2
    modelos = {d["name"]: d["model"] for d in listed.json()}
    assert modelos["olt-norte"] == "smartax"
    # Columna vacía: se queda con el valor por defecto, MikroTik.
    assert modelos["rb-sur"] == "routeros"


async def test_import_rejects_an_unusable_platform(auth_headers) -> None:
    async with client(auth_headers) as api:
        response = await api.post(
            "/api/devices/import",
            headers=auth_headers,
            json={"text": "rb-malo,192.0.2.42,22,u,c,EmpresaA,Modelo Raro!"},
        )

    assert response.json()["created"] == 0
    assert response.json()["errors"][0]["message"].startswith("Valor inválido")
