"""Extracción de credenciales PPPoE del respaldo.

Es la función más sensible del panel —entrega contraseñas de clientes—, así
que se prueba tanto lo que extrae como quién puede verlo y que quede anotado.
"""

import subprocess

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.config import get_settings
from app.credentials import extract_pppoe


pytestmark = pytest.mark.anyio

EXPORT_CON_CLAVES = """# oct/01/2026 08:00:00 by RouterOS 7.23.2
/ppp profile
add name=plan-20m rate-limit=20M/20M
/ppp secret
add name=cliente-0001 password="Clave-0001" profile=plan-20m service=pppoe \\
    remote-address=100.64.0.10 comment="Calle Primera 123"
add name=cliente-0002 password="Clave-0002" profile=plan-20m service=pppoe
add name=cliente-0003 password="Clave-0003" profile=plan-20m disabled=yes
/interface pppoe-client
add name=pppoe-wan user=enlace-mayorista password="ClaveEnlace" interface=ether1
"""

# Lo que devuelve un router cuya cuenta de respaldo NO tiene la policy
# `sensitive`: están los usuarios pero sin contraseña.
EXPORT_CENSURADO = """# oct/01/2026 08:00:00 by RouterOS 7.23.2
/ppp secret
add name=cliente-0001 profile=plan-20m service=pppoe
add name=cliente-0002 profile=plan-20m service=pppoe
"""


def client() -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url="http://test")


# --- Extracción pura -------------------------------------------------------


def test_extracts_users_passwords_and_details() -> None:
    resultado = extract_pppoe(EXPORT_CON_CLAVES)

    assert resultado["total"] == 3
    assert resultado["with_password"] == 3
    assert resultado["censored"] is False
    primero = resultado["secrets"][0]
    assert primero["user"] == "cliente-0001"
    assert primero["password"] == "Clave-0001"
    assert primero["profile"] == "plan-20m"
    assert primero["remote_address"] == "100.64.0.10"
    assert primero["comment"] == "Calle Primera 123"
    assert primero["enabled"] is True


def test_disabled_accounts_are_marked() -> None:
    resultado = extract_pppoe(EXPORT_CON_CLAVES)
    tercero = next(s for s in resultado["secrets"] if s["user"] == "cliente-0003")

    assert tercero["enabled"] is False


def test_pppoe_client_connections_are_listed_apart() -> None:
    resultado = extract_pppoe(EXPORT_CON_CLAVES)

    assert len(resultado["clients"]) == 1
    assert resultado["clients"][0]["user"] == "enlace-mayorista"
    assert resultado["clients"][0]["password"] == "ClaveEnlace"


def test_users_come_sorted() -> None:
    usuarios = [s["user"] for s in extract_pppoe(EXPORT_CON_CLAVES)["secrets"]]

    assert usuarios == sorted(usuarios)


def test_a_censored_export_is_detected() -> None:
    """Sin la policy `sensitive` salen los usuarios pero no las claves."""
    resultado = extract_pppoe(EXPORT_CENSURADO)

    assert resultado["total"] == 2
    assert resultado["with_password"] == 0
    assert resultado["censored"] is True


def test_a_router_without_pppoe_is_not_reported_as_censored() -> None:
    resultado = extract_pppoe("/ip address\nadd address=192.0.2.1/24\n")

    assert resultado["total"] == 0
    assert resultado["censored"] is False


# --- Endpoint --------------------------------------------------------------


@pytest.fixture
def repo_con_respaldo(tmp_path):
    repo = str(tmp_path / "backups")
    subprocess.run(["git", "init", "-q", repo], check=True, capture_output=True)
    (tmp_path / "backups" / "rb-uno").write_text(EXPORT_CON_CLAVES, encoding="utf-8")
    for args in (
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "r"],
    ):
        subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)
    settings = get_settings()
    original = settings.oxidized_backup_repo
    settings.oxidized_backup_repo = repo
    yield repo
    settings.oxidized_backup_repo = original


@pytest.fixture
async def equipo(auth_headers):
    async with client() as api:
        await api.post(
            "/api/devices",
            headers=auth_headers,
            json={"name": "rb-uno", "address": "192.0.2.1", "group_name": "EmpresaA"},
        )


async def test_operator_gets_the_credentials(
    auth_headers, equipo, repo_con_respaldo, make_user
) -> None:
    headers = make_user("op-a", role="operador", group_name="EmpresaA")

    async with client() as api:
        response = await api.get("/api/backups/pppoe?node=rb-uno", headers=headers)

    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert response.json()["secrets"][0]["password"] == "Clave-0001"


@pytest.mark.parametrize("role", ["auditor", "lector"])
async def test_read_only_roles_cannot_see_credentials(
    auth_headers, equipo, repo_con_respaldo, make_user, role
) -> None:
    headers = make_user("cuenta", role=role, group_name="EmpresaA")

    async with client() as api:
        response = await api.get("/api/backups/pppoe?node=rb-uno", headers=headers)

    assert response.status_code == 403


async def test_another_company_cannot_see_them(
    auth_headers, equipo, repo_con_respaldo, make_user
) -> None:
    headers = make_user("op-b", role="operador", group_name="EmpresaB")

    async with client() as api:
        response = await api.get("/api/backups/pppoe?node=rb-uno", headers=headers)

    assert response.status_code == 404


async def test_the_query_is_written_to_the_activity_log(
    auth_headers, equipo, repo_con_respaldo, activity_repository
) -> None:
    async with client() as api:
        await api.get("/api/backups/pppoe?node=rb-uno", headers=auth_headers)

    entrada = activity_repository.entries[-1]
    assert entrada["action"] == "credenciales.pppoe"
    assert entrada["target"] == "rb-uno"
    assert "3 usuarios" in entrada["detail"]
    # La bitácora anota que se consultaron, nunca las claves.
    assert "Clave-0001" not in repr(activity_repository.entries)


async def test_a_device_without_backup_says_so(auth_headers, repo_con_respaldo) -> None:
    async with client() as api:
        await api.post(
            "/api/devices",
            headers=auth_headers,
            json={"name": "rb-sin-respaldo", "address": "192.0.2.2"},
        )
        response = await api.get(
            "/api/backups/pppoe?node=rb-sin-respaldo", headers=auth_headers
        )

    assert response.status_code == 404
    assert "respaldo" in response.json()["detail"]
