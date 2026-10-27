"""Servidor de desarrollo del panel, con datos de ejemplo en memoria.

Arranca el backend de verdad (mismas rutas, permisos, CSP y cabeceras) pero
cambia PostgreSQL por los repositorios en memoria de las pruebas y simula
Oxidized. Sirve para ver y probar la interfaz sin Docker. NO es para
producción: no guarda nada y la clave del administrador es fija.

    pip install -r backend/requirements-dev.txt
    python panel/dev_server.py            # http://127.0.0.1:8000
    usuario: admin   clave: demo-admin-123
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(RAIZ / "backend"), str(RAIZ / "backend" / "tests")]

PUERTO = int(os.environ.get("PANEL_PORT", "8000"))

# Repositorio de respaldos de ejemplo, en una carpeta temporal que se borra
# al cerrar: así funcionan «Cambios», «Último respaldo» y la Auditoría.
REPO_DEMO = tempfile.TemporaryDirectory(prefix="oxm-demo-")

os.environ.update(
    {
        "APP_ENV": "development",
        "APP_SECRET_KEY": "dev-panel-only-0123456789abcdef0123456789abcdef",
        "POSTGRES_HOST": "localhost",
        "POSTGRES_DB": "dev",
        "POSTGRES_USER": "dev",
        "POSTGRES_PASSWORD": "dev",
        "REDIS_HOST": "localhost",
        "REDIS_PASSWORD": "dev",
        # Oxidized simulado: el propio servidor responde /nodes.json.
        "OXIDIZED_URL": f"http://127.0.0.1:{PUERTO}",
        "OXIDIZED_SOURCE_TOKEN": "dev-token",
        "OXIDIZED_BACKUP_REPO": REPO_DEMO.name,
    }
)

import uvicorn  # noqa: E402

from app import devices as devices_module  # noqa: E402
from app import main as main_module  # noqa: E402
from app.security import hash_password  # noqa: E402
from conftest import (  # noqa: E402
    FakeAccessRepository,
    FakeActivityRepository,
    FakeBackupEventRepository,
    FakeBackupIntegrityRepository,
    FakeDeviceRepository,
    FakeSettingsRepository,
    FakeUserRepository,
)

ADMIN_CLAVE = "demo-admin-123"

EQUIPOS = [
    # nombre, ip, empresa, identity, versión, placa, estado de Oxidized
    ("rb-core-01", "10.10.0.1", "FibraNet", "CORE-UIO", "7.16.2", "CCR2116-12G-4S+", "success"),
    ("rb-core-02", "10.10.0.2", "FibraNet", "CORE-GYE", "7.16.2", "CCR2216-1G-12XS-2XQ", "success"),
    ("rb-olt-norte", "10.10.4.10", "FibraNet", "OLT-NORTE", "7.15.3", "CCR2004-16G-2S+", "success"),
    ("rb-pppoe-sur", "10.10.8.1", "FibraNet", "PPPOE-SUR", "6.49.17", "CCR1036-8G-2S+", "no_connection"),
    ("rb-borde-bgp", "200.1.1.1", "AndesLink", "EDGE-BGP", "7.16.1", "CCR2216-1G-12XS-2XQ", "success"),
    ("rb-agg-centro", "172.16.0.1", "AndesLink", "AGG-CENTRO", "7.14.3", "CCR2004-1G-12S+2XS", "success"),
    ("rb-wisp-cumbaya", "172.16.20.1", "AndesLink", "WISP-CUMBAYA", "6.48.6", "RB4011iGS+", "never"),
    ("rb-sucursal-01", "192.168.50.1", "Cooperativa Sur", "SUC-01", "7.12.1", "RB5009UG+S+", "success"),
    ("rb-sucursal-02", "192.168.51.1", "Cooperativa Sur", "SUC-02", "7.12.1", "RB5009UG+S+", "timeout"),
    ("rb-matriz", "192.168.1.1", "Cooperativa Sur", "MATRIZ", "7.16.2", "CCR2004-16G-2S+", "success"),
    ("hap-lab", "192.168.88.1", "", "", "", "", "never"),
]


def export_routeros(nombre: str, identity: str, version: str, placa: str, i: int, revision: int) -> str:
    """Un `/export` verosímil. Los equipos impares dejan servicios abiertos para
    que la auditoría tenga algo que contar; la revisión 2 cambia el firewall."""
    inseguro = i % 2 == 1
    lineas = [
        f"# {datetime.now():%Y-%m-%d %H:%M:%S} by RouterOS {version}",
        f"# software id = DEMO-{i:04d}",
        "#",
        f"# model = {placa}",
        f"# serial number = HG{i:02d}0A1B2C3D",
        "/interface bridge",
        "add name=bridge-lan",
        "/interface ethernet",
        "set [ find default-name=ether1 ] comment=WAN",
        "/ip address",
        f"add address=10.{i}.0.1/24 interface=bridge-lan network=10.{i}.0.0",
        "/ip dns",
        f"set allow-remote-requests={'yes' if inseguro else 'no'} servers=1.1.1.1,8.8.8.8",
        "/ip firewall filter",
        "add action=accept chain=input connection-state=established,related",
        "add action=accept chain=input protocol=icmp",
    ]
    if revision == 2:
        lineas.append("add action=accept chain=input src-address=10.99.99.0/24 comment=gestion")
    if not inseguro or revision == 2 and i % 4 == 1:
        lineas.append('add action=drop chain=input in-interface-list=!LAN comment="drop resto"')
    lineas += [
        "/ip service",
        f"set telnet disabled={'no' if inseguro else 'yes'}",
        f"set ftp disabled={'no' if inseguro else 'yes'}",
        "set www disabled=yes",
        f"set winbox address={'' if inseguro else '10.99.99.0/24'}",
        "/snmp community",
        f"set [ find default=yes ] name={'public' if inseguro else 'n0c-ro-' + str(i)}",
        "/system identity",
        f"set name={identity or nombre}",
        "/system ntp client",
        f"set enabled={'yes' if revision == 2 else 'no'}",
        "/user",
        'add group=read name=oxidized-ro password="********"',
    ]
    return "\n".join(lineas) + "\n"


def crear_repo_demo() -> None:
    repo = REPO_DEMO.name
    git = lambda *a, **kw: subprocess.run(  # noqa: E731
        ["git", "-C", repo, *a], check=True, capture_output=True, **kw
    )
    git("init", "-q")
    git("config", "user.name", "oxidized")
    git("config", "user.email", "backup@demo.local")
    hace_dias = datetime.now(timezone.utc) - timedelta(days=3)
    for revision in (1, 2):
        for i, (nombre, _ip, _empresa, identity, version, placa, estado) in enumerate(EQUIPOS):
            if estado == "never":
                continue
            Path(repo, nombre).write_text(
                export_routeros(nombre, identity, version, placa, i, revision), encoding="utf-8"
            )
            git("add", nombre)
            fecha = (hace_dias + timedelta(days=revision, minutes=i * 7)).isoformat()
            git(
                "commit", "-q", "-m", f"update {nombre}",
                env={**os.environ, "GIT_AUTHOR_DATE": fecha, "GIT_COMMITTER_DATE": fecha},
            )


async def sembrar(app) -> None:
    crear_repo_demo()
    devices = FakeDeviceRepository()
    events = FakeBackupEventRepository()
    users = FakeUserRepository()
    users._users["admin"] = {
        "id": 1,
        "username": "admin",
        "password_hash": hash_password(ADMIN_CLAVE),
        "must_change_password": False,
        "role": "admin",
        "group_name": "",
    }

    ahora = datetime.now(timezone.utc)
    for i, (nombre, ip, empresa, identity, version, placa, estado) in enumerate(EQUIPOS):
        await devices.create_device(
            {
                "name": nombre,
                "address": ip,
                "port": 22 if i % 3 else 8022,
                "model": "routeros",
                "username": "oxidized-ro",
                "password": "x",
                "enabled": nombre != "hap-lab",
                "group_name": empresa,
                "backup_interval_minutes": 0,
                "identity": identity,
                "ros_version": version,
                "board": placa,
                # El repo de demo trae commits de días atrás: sin esto el
                # historial anterior al alta quedaría oculto.
                "history_since": None,
            }
        )
        if estado == "never":
            continue
        await events.record_event(nombre, "node_success", f"{i:02x}a7c3e91b4d8f02{i:02x}")
        if estado != "success":
            await events.record_event(nombre, "node_fail", "")
        # Fechas escalonadas para que la tabla no salga con la misma hora.
        for j, evento in enumerate(events._events[-2:]):
            evento["created_at"] = ahora - timedelta(minutes=17 * i + 5 * j)

    app.state.devices = devices
    app.state.backup_events = events
    app.state.backup_integrity = FakeBackupIntegrityRepository()
    app.state.activity = FakeActivityRepository()
    app.state.access = FakeAccessRepository()
    app.state.settings = FakeSettingsRepository()
    app.state.users = users
    app.state.estados_demo = {nombre: estado for nombre, *_, estado in EQUIPOS}


@asynccontextmanager
async def lifespan_demo(app):
    await sembrar(app)
    yield


async def sin_respaldo(oxidized_url: str, names: list[str]) -> None:
    """En la demo no hay Oxidized al que pedirle un respaldo."""


app = main_module.app
app.router.lifespan_context = lifespan_demo
devices_module.backup_new_nodes = sin_respaldo


@app.get("/nodes.json", include_in_schema=False)
async def oxidized_simulado() -> list[dict]:
    estados = getattr(app.state, "estados_demo", {})
    nombres = [d["name"] for d in await app.state.devices.list_devices()]
    return [{"name": n, "status": estados.get(n, "never"), "last": None} for n in nombres]


if __name__ == "__main__":
    print(f"\n  Panel de demostración: http://127.0.0.1:{PUERTO}")
    print(f"  usuario: admin   clave: {ADMIN_CLAVE}\n")
    uvicorn.run(app, host="127.0.0.1", port=PUERTO, log_level="warning")
