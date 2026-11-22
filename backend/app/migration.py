"""Exportar e importar la base para mudar el panel a otra instalación.

Las claves de los routers se guardan cifradas con `APP_SECRET_KEY`, que es
propia de cada instalación: un volcado de PostgreSQL llevado a otro servidor
trae claves que allí no se pueden descifrar. Por eso el paquete de migración no
es un volcado: se descifra todo con la llave de origen, se sella con una frase
que escribe el administrador y, al importar, se vuelve a cifrar con la llave de
destino.

Qué viaja: inventario de equipos (con todas sus credenciales), cuentas de
acceso y ajustes. Qué no: el historial de configuraciones (`backups.git`), los
eventos de respaldo y la bitácora, que son de la instalación de origen; para
llevarse también eso está `scripts/backup-system.sh`.
"""

import base64
import json
import os
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .auth import require_admin
from .config import get_settings
from .repository import (
    SETTINGS_DEFAULTS,
    ClaveIlegibleError,
    DuplicateDeviceError,
    DuplicateUserError,
)
from .scheduler import refresh_inventory
from .schemas import ROLES


FORMATO = "oxidized-ai-manager-migracion"
VERSION = 1
ITERACIONES = 600_000
FRASE_MINIMA = 12

# Ajustes que describen lo último que hizo ESTA instalación, no cómo está
# configurada: no tiene sentido llevarlos a otra.
AJUSTES_DE_ESTADO = frozenset(
    {
        "last_push_ok",
        "last_push_at",
        "last_push_detail",
        "last_stale_alert_at",
        "last_stale_alert_detail",
    }
)

CAMPOS_EQUIPO = (
    "name",
    "address",
    "port",
    "model",
    "enabled",
    "group_name",
    "backup_interval_minutes",
)


class PaqueteInvalidoError(Exception):
    """El archivo no es un paquete de migración o la frase no lo abre."""


def _llave(frase: str, sal: bytes, iteraciones: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=sal, iterations=iteraciones
    )
    return base64.urlsafe_b64encode(kdf.derive(frase.encode("utf-8")))


def sellar(frase: str, contenido: dict[str, Any]) -> dict[str, Any]:
    """Cifra `contenido` con una llave derivada de la frase (PBKDF2 + Fernet)."""
    sal = os.urandom(16)
    token = Fernet(_llave(frase, sal, ITERACIONES)).encrypt(
        json.dumps(contenido, ensure_ascii=False).encode("utf-8")
    )
    return {
        "formato": FORMATO,
        "version": VERSION,
        "creado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kdf": "pbkdf2-sha256",
        "iteraciones": ITERACIONES,
        "sal": base64.b64encode(sal).decode(),
        "datos": token.decode(),
    }


def abrir(frase: str, paquete: dict[str, Any]) -> dict[str, Any]:
    """Descifra un paquete. Fernet autentica: un archivo alterado no abre."""
    if not isinstance(paquete, dict) or paquete.get("formato") != FORMATO:
        raise PaqueteInvalidoError("El archivo no es un paquete de migración.")
    if paquete.get("version") != VERSION:
        raise PaqueteInvalidoError(
            "El paquete es de otra versión del formato; actualice ambas "
            "instalaciones a la misma versión y vuelva a exportar."
        )
    try:
        sal = base64.b64decode(paquete["sal"])
        iteraciones = int(paquete["iteraciones"])
        if not 100_000 <= iteraciones <= 5_000_000:
            raise ValueError("iteraciones fuera de rango")
        claro = Fernet(_llave(frase, sal, iteraciones)).decrypt(
            str(paquete["datos"]).encode()
        )
        contenido = json.loads(claro)
    except InvalidToken:
        raise PaqueteInvalidoError(
            "La frase no corresponde a este paquete, o el archivo está dañado."
        ) from None
    except (KeyError, ValueError, TypeError):
        raise PaqueteInvalidoError("El paquete está incompleto o dañado.") from None
    if not isinstance(contenido, dict):
        raise PaqueteInvalidoError("El paquete está incompleto o dañado.")
    return contenido


class ExportRequest(BaseModel):
    frase: str = Field(min_length=FRASE_MINIMA, max_length=200)


class ImportRequest(BaseModel):
    frase: str = Field(min_length=1, max_length=200)
    paquete: dict[str, Any]
    importar_ajustes: bool = True


router = APIRouter(
    prefix="/api/system/migration",
    tags=["system"],
    dependencies=[Depends(require_admin)],
)


async def _exportar_equipos(repo: Any) -> list[dict[str, Any]]:
    equipos = []
    for device in await repo.list_devices():
        acceso = await repo.all_credentials(device["id"])
        if acceso is None:  # borrado entre las dos consultas
            continue
        equipos.append(
            {
                **{campo: device.get(campo) for campo in CAMPOS_EQUIPO},
                "active_credential": acceso["active_credential"],
                "credentials": [
                    {"username": c["username"], "password": c["password"]}
                    for c in acceso["credentials"]
                ],
            }
        )
    return equipos


@router.post("/export")
async def exportar(request: Request, payload: ExportRequest) -> JSONResponse:
    """Paquete cifrado con equipos, cuentas y ajustes, listo para descargar."""
    state = request.app.state
    try:
        equipos = await _exportar_equipos(state.devices)
    except ClaveIlegibleError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Hay claves de equipos cifradas con otra APP_SECRET_KEY y no se "
                "pueden leer. Restaure la llave anterior o vuelva a guardar esas "
                "claves antes de exportar."
            ),
        ) from None

    cuentas = []
    for publico in await state.users.list_users():
        cuenta = await state.users.get_by_username(publico["username"])
        if cuenta is None:
            continue
        cuentas.append(
            {
                "username": cuenta["username"],
                "password_hash": cuenta["password_hash"],
                "must_change_password": bool(cuenta.get("must_change_password")),
                "role": cuenta.get("role") or "lector",
                "group_name": cuenta.get("group_name") or "",
            }
        )

    ajustes = {
        clave: valor
        for clave, valor in (await state.settings.get_all()).items()
        if clave not in AJUSTES_DE_ESTADO
    }

    paquete = sellar(
        payload.frase,
        {"equipos": equipos, "cuentas": cuentas, "ajustes": ajustes},
    )
    paquete["resumen"] = {"equipos": len(equipos), "cuentas": len(cuentas)}
    nombre = "oxidized-ai-manager-migracion-{}.json".format(
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    )
    return JSONResponse(
        paquete,
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"',
            "Cache-Control": "no-store",
        },
    )


def _datos_equipo(equipo: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Valida un equipo del paquete y lo deja como lo espera el repositorio."""
    nombre = str(equipo.get("name") or "").strip()
    direccion = str(equipo.get("address") or "").strip()
    if not nombre or not direccion:
        raise ValueError("sin nombre o sin dirección")
    credenciales = [
        {"username": str(c.get("username") or ""), "password": str(c.get("password") or "")}
        for c in (equipo.get("credentials") or [])
        if isinstance(c, dict)
    ]
    principal = credenciales[0] if credenciales else {"username": "", "password": ""}
    puerto = int(equipo.get("port") or 22)
    if not 1 <= puerto <= 65535:
        raise ValueError("puerto fuera de rango")
    datos = {
        "name": nombre,
        "address": direccion,
        "port": puerto,
        "model": str(equipo.get("model") or "routeros"),
        "username": principal["username"],
        "password": principal["password"],
        "extra_credentials": credenciales[1:],
        "enabled": bool(equipo.get("enabled", True)),
        "group_name": str(equipo.get("group_name") or ""),
        "backup_interval_minutes": max(0, int(equipo.get("backup_interval_minutes") or 0)),
    }
    activa = int(equipo.get("active_credential") or 1)
    if not 1 <= activa <= max(1, len(credenciales)):
        activa = 1
    return datos, activa


@router.post("/import")
async def importar(
    request: Request, payload: ImportRequest, background: BackgroundTasks
) -> dict:
    """Carga un paquete exportado desde otra instalación.

    Equipos: los que no existen se crean y los que coinciden por nombre se
    actualizan. Cuentas: solo se crean las que faltan; una cuenta que ya existe
    aquí no se toca, para no cambiarle la contraseña a quien está importando.
    Ajustes: se copian, pero el envío al Git remoto queda desactivado (ver más
    abajo).
    """
    try:
        contenido = abrir(payload.frase, payload.paquete)
    except PaqueteInvalidoError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        ) from None

    state = request.app.state
    resultado: dict[str, Any] = {
        "equipos_creados": 0,
        "equipos_actualizados": 0,
        "cuentas_creadas": 0,
        "cuentas_omitidas": 0,
        "ajustes_importados": 0,
        "errores": [],
    }

    for equipo in contenido.get("equipos") or []:
        nombre = str((equipo or {}).get("name") or "?")
        try:
            datos, activa = _datos_equipo(equipo)
            existente = await state.devices.get_device_by_name(datos["name"])
            if existente is None:
                creado = await state.devices.create_device(datos)
                device_id = creado["id"]
                resultado["equipos_creados"] += 1
            else:
                device_id = existente["id"]
                # Sin `name`: no cambia, y enviarlo reiniciaría el historial.
                cambios = {k: v for k, v in datos.items() if k != "name"}
                await state.devices.update_device(device_id, cambios)
                resultado["equipos_actualizados"] += 1
            await state.devices.set_active_credential(device_id, activa)
        except (ValueError, TypeError, AttributeError, DuplicateDeviceError) as error:
            resultado["errores"].append(f"Equipo {nombre}: {error or 'datos inválidos'}")

    for cuenta in contenido.get("cuentas") or []:
        usuario = str((cuenta or {}).get("username") or "").strip()
        hash_clave = str((cuenta or {}).get("password_hash") or "")
        rol = str((cuenta or {}).get("role") or "lector")
        if not usuario or not hash_clave or rol not in ROLES:
            resultado["errores"].append(f"Cuenta {usuario or '?'}: datos inválidos")
            continue
        if await state.users.get_by_username(usuario) is not None:
            resultado["cuentas_omitidas"] += 1
            continue
        try:
            await state.users.create_user(
                usuario,
                hash_clave,
                bool(cuenta.get("must_change_password")),
                rol,
                str(cuenta.get("group_name") or ""),
            )
            resultado["cuentas_creadas"] += 1
        except DuplicateUserError:
            resultado["cuentas_omitidas"] += 1

    if payload.importar_ajustes:
        ajustes = {
            clave: str(valor)
            for clave, valor in (contenido.get("ajustes") or {}).items()
            if clave in SETTINGS_DEFAULTS and clave not in AJUSTES_DE_ESTADO
        }
        # Dos ajustes se copian apagados y se encienden a mano tras revisar:
        # - Git remoto: esta instalación empieza con un `backups.git` propio; si
        #   empujara al mismo remoto que la de origen mientras las dos siguen
        #   vivas, chocarían los historiales.
        # - Lista de IPs permitidas para ingresar: es la del servidor de origen
        #   y aquí podría dejar fuera a quien está importando.
        apagados = []
        for interruptor in ("git_remote_enabled", "login_allowlist_enabled"):
            if ajustes.get(interruptor) == "true":
                ajustes[interruptor] = "false"
                apagados.append(interruptor)
        resultado["ajustes_apagados"] = apagados
        if ajustes:
            await state.settings.set_many(ajustes)
        resultado["ajustes_importados"] = len(ajustes)

    if resultado["equipos_creados"] or resultado["equipos_actualizados"]:
        background.add_task(refresh_inventory, get_settings().oxidized_url)
    # La lista de errores se acota: va a la respuesta y no debe crecer sin fin.
    resultado["errores"] = resultado["errores"][:50]
    return resultado
