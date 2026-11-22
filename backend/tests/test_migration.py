"""Exportar la base e importarla en otra instalación (app/migration.py)."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app import migration
from app.security import hash_password, verify_password


pytestmark = pytest.mark.anyio

FRASE = "frase-de-prueba-larga"
EXPORT = "/api/system/migration/export"
IMPORT = "/api/system/migration/import"


def client() -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def sin_recarga_de_oxidized(monkeypatch):
    llamadas = []

    async def fake_refresh(url: str) -> None:
        llamadas.append(url)

    monkeypatch.setattr(migration, "refresh_inventory", fake_refresh)
    return llamadas


async def _sembrar(device_repository, user_repository, settings_repository) -> None:
    await device_repository.create_device(
        {
            "name": "rb-lab-01",
            "address": "192.0.2.10",
            "port": 2222,
            "model": "routeros",
            "username": "backup",
            "password": "clave-principal",
            "extra_credentials": [{"username": "backup2", "password": "clave-extra"}],
            "enabled": False,
            "group_name": "Empresa A",
            "backup_interval_minutes": 30,
        }
    )
    await device_repository.set_active_credential(1, 2)
    await user_repository.create_user(
        "operador1", hash_password("secreta-operador"), False, "operador", "Empresa A"
    )
    await settings_repository.set_many(
        {
            "backup_interval_minutes": "45",
            "telegram_bot_token": "token-de-prueba",
            "git_remote_enabled": "true",
            "git_remote_url": "https://example.invalid/repo.git",
            "login_allowlist_enabled": "true",
            "login_allowlist": "192.0.2.0/24",
            "last_push_detail": "estado de la instalación de origen",
        }
    )


async def _exportar(auth_headers, frase: str = FRASE) -> dict:
    async with client() as api:
        response = await api.post(EXPORT, headers=auth_headers, json={"frase": frase})
    assert response.status_code == 200, response.text
    assert "attachment" in response.headers["content-disposition"]
    return response.json()


def _vaciar(device_repository, user_repository, settings_repository) -> None:
    """Deja los repositorios como una instalación recién hecha (solo admin)."""
    device_repository._devices.clear()
    device_repository._next_id = 1
    for nombre in [u for u in user_repository._users if u != "admin"]:
        del user_repository._users[nombre]
    settings_repository._values.clear()


async def test_requiere_administrador(make_user) -> None:
    lector = make_user("lector1", role="lector")
    async with client() as api:
        anonimo = await api.post(EXPORT, json={"frase": FRASE})
        sin_permiso = await api.post(EXPORT, headers=lector, json={"frase": FRASE})
        importar = await api.post(
            IMPORT, headers=lector, json={"frase": FRASE, "paquete": {}}
        )
    assert anonimo.status_code == 401
    assert sin_permiso.status_code == 403
    assert importar.status_code == 403


async def test_frase_corta_se_rechaza(auth_headers) -> None:
    async with client() as api:
        response = await api.post(EXPORT, headers=auth_headers, json={"frase": "corta"})
    assert response.status_code == 422


async def test_el_paquete_no_lleva_nada_en_claro(
    auth_headers, device_repository, user_repository, settings_repository
) -> None:
    await _sembrar(device_repository, user_repository, settings_repository)
    paquete = await _exportar(auth_headers)

    texto = json.dumps(paquete)
    for secreto in ("clave-principal", "clave-extra", "token-de-prueba", "192.0.2.10"):
        assert secreto not in texto
    assert paquete["resumen"] == {"equipos": 1, "cuentas": 2}


async def test_ida_y_vuelta_a_una_instalacion_vacia(
    auth_headers,
    device_repository,
    user_repository,
    settings_repository,
    sin_recarga_de_oxidized,
) -> None:
    await _sembrar(device_repository, user_repository, settings_repository)
    paquete = await _exportar(auth_headers)
    _vaciar(device_repository, user_repository, settings_repository)
    hash_admin = user_repository._users["admin"]["password_hash"]

    async with client() as api:
        response = await api.post(
            IMPORT, headers=auth_headers, json={"frase": FRASE, "paquete": paquete}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["equipos_creados"] == 1
    assert body["equipos_actualizados"] == 0
    assert body["cuentas_creadas"] == 1
    assert body["cuentas_omitidas"] == 1  # admin ya existía
    assert body["errores"] == []

    equipo = await device_repository.get_device_by_name("rb-lab-01")
    assert equipo["address"] == "192.0.2.10"
    assert equipo["port"] == 2222
    assert equipo["enabled"] is False
    assert equipo["group_name"] == "Empresa A"
    assert equipo["backup_interval_minutes"] == 30
    acceso = await device_repository.all_credentials(equipo["id"])
    assert acceso["active_credential"] == 2
    assert [(c["username"], c["password"]) for c in acceso["credentials"]] == [
        ("backup", "clave-principal"),
        ("backup2", "clave-extra"),
    ]

    operador = user_repository._users["operador1"]
    assert verify_password("secreta-operador", operador["password_hash"])
    assert operador["role"] == "operador"
    assert operador["group_name"] == "Empresa A"
    # La cuenta de quien importa no se toca.
    assert user_repository._users["admin"]["password_hash"] == hash_admin

    ajustes = await settings_repository.get_all()
    assert ajustes["backup_interval_minutes"] == "45"
    assert ajustes["telegram_bot_token"] == "token-de-prueba"
    assert ajustes["git_remote_url"] == "https://example.invalid/repo.git"
    # Se copian apagados: ver el comentario en `importar`.
    assert ajustes["git_remote_enabled"] == "false"
    assert ajustes["login_allowlist_enabled"] == "false"
    assert ajustes["login_allowlist"] == "192.0.2.0/24"
    assert sorted(body["ajustes_apagados"]) == [
        "git_remote_enabled",
        "login_allowlist_enabled",
    ]
    # El estado de la instalación de origen no viaja.
    assert ajustes["last_push_detail"] == ""
    assert sin_recarga_de_oxidized, "debe recargar el inventario de Oxidized"


async def test_equipo_existente_se_actualiza_sin_duplicar(
    auth_headers, device_repository, user_repository, settings_repository
) -> None:
    await _sembrar(device_repository, user_repository, settings_repository)
    paquete = await _exportar(auth_headers)
    await device_repository.update_device(
        1, {"address": "192.0.2.99", "password": "clave-vieja"}
    )

    async with client() as api:
        response = await api.post(
            IMPORT,
            headers=auth_headers,
            json={"frase": FRASE, "paquete": paquete, "importar_ajustes": False},
        )

    body = response.json()
    assert body["equipos_creados"] == 0
    assert body["equipos_actualizados"] == 1
    assert body["ajustes_importados"] == 0
    assert len(await device_repository.list_devices()) == 1
    acceso = await device_repository.all_credentials(1)
    assert acceso["address"] == "192.0.2.10"
    assert acceso["credentials"][0]["password"] == "clave-principal"


async def test_frase_equivocada_no_importa_nada(
    auth_headers, device_repository, user_repository, settings_repository
) -> None:
    await _sembrar(device_repository, user_repository, settings_repository)
    paquete = await _exportar(auth_headers)
    _vaciar(device_repository, user_repository, settings_repository)

    async with client() as api:
        response = await api.post(
            IMPORT,
            headers=auth_headers,
            json={"frase": "otra-frase-distinta", "paquete": paquete},
        )

    assert response.status_code == 400
    assert "frase" in response.json()["detail"].lower()
    assert await device_repository.list_devices() == []


async def test_paquete_alterado_o_ajeno_se_rechaza(
    auth_headers, device_repository, user_repository, settings_repository
) -> None:
    await _sembrar(device_repository, user_repository, settings_repository)
    paquete = await _exportar(auth_headers)
    alterado = {**paquete, "datos": paquete["datos"][:-6] + "AAAAAA"}

    async with client() as api:
        roto = await api.post(
            IMPORT, headers=auth_headers, json={"frase": FRASE, "paquete": alterado}
        )
        ajeno = await api.post(
            IMPORT,
            headers=auth_headers,
            json={"frase": FRASE, "paquete": {"cualquier": "cosa"}},
        )

    assert roto.status_code == 400
    assert ajeno.status_code == 400


async def test_un_equipo_invalido_no_frena_a_los_demas(
    auth_headers, device_repository
) -> None:
    paquete = migration.sellar(
        FRASE,
        {
            "equipos": [
                {"name": "", "address": "192.0.2.1"},
                {
                    "name": "rb-lab-02",
                    "address": "192.0.2.2",
                    "port": 22,
                    "credentials": [{"username": "backup", "password": "x"}],
                    "active_credential": 9,
                },
            ],
            "cuentas": [{"username": "raro", "password_hash": "h", "role": "dios"}],
            "ajustes": {"clave_inventada": "1"},
        },
    )

    async with client() as api:
        response = await api.post(
            IMPORT, headers=auth_headers, json={"frase": FRASE, "paquete": paquete}
        )

    body = response.json()
    assert body["equipos_creados"] == 1
    assert body["cuentas_creadas"] == 0
    assert body["ajustes_importados"] == 0
    assert len(body["errores"]) == 2
    acceso = await device_repository.all_credentials(1)
    assert acceso["active_credential"] == 1  # fuera de rango -> la principal
