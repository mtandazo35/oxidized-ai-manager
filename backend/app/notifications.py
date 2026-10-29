"""Avisos por Telegram: equipos sin respaldo reciente.

El canal es un bot de Telegram (token + chat_id). El envío es un único GET a
`api.telegram.org`; `httpx` ya está en el proyecto. Todo es configurable desde
el panel (Ajustes del sistema) y nada va hardcodeado.
"""

import datetime as dt
import logging

import httpx

log = logging.getLogger("oxidized-ai-manager.notifications")

TELEGRAM_API = "https://api.telegram.org"
# Telegram corta los mensajes en 4096 caracteres; listar 172 equipos no cabe y
# además no se lee. Se listan los primeros y se resume el resto.
MAX_LISTED = 40


async def send_telegram(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    """Envía un mensaje. Devuelve (ok, detalle). Nunca lanza."""
    token = (token or "").strip()
    chat_id = (chat_id or "").strip()
    if not token or not chat_id:
        return False, "Falta el token del bot o el chat_id."
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
    except httpx.HTTPError as error:
        return False, f"No se pudo contactar a Telegram: {error}"
    if response.status_code == 200 and response.json().get("ok"):
        return True, "ok"
    # Telegram explica el motivo en `description` (token malo, chat no iniciado…).
    try:
        detalle = response.json().get("description") or response.text
    except ValueError:
        detalle = response.text
    return False, f"Telegram respondió {response.status_code}: {detalle}"[:300]


def stale_devices(
    devices: list[dict],
    statuses: dict[str, dict],
    threshold_hours: float,
    now: dt.datetime | None = None,
) -> list[dict]:
    """Equipos habilitados cuyo último respaldo exitoso es más viejo que el
    umbral, o que nunca se respaldaron. Cada entrada: name, group, last, horas.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    limite = dt.timedelta(hours=max(threshold_hours, 1))
    atrasados = []
    for device in devices:
        if not device.get("enabled"):
            continue
        last = (statuses.get(device["name"]) or {}).get("last_success_at")
        if last is not None and now - last <= limite:
            continue
        horas = None if last is None else (now - last).total_seconds() / 3600
        atrasados.append(
            {
                "name": device["name"],
                "group": device.get("group_name") or "Sin empresa",
                "last": last,
                "horas": horas,
            }
        )
    # Primero los que nunca respaldaron, luego del más atrasado al menos.
    atrasados.sort(key=lambda d: (d["horas"] is not None, d["horas"] or 0))
    return atrasados


def build_stale_message(
    atrasados: list[dict],
    threshold_hours: float,
    total_enabled: int,
    now: dt.datetime | None = None,
) -> str:
    """Arma el texto del aviso, agrupado por empresa y con tope de líneas."""
    now = now or dt.datetime.now(dt.timezone.utc)
    ecuador = now - dt.timedelta(hours=5)
    horas = int(threshold_hours)
    cabecera = (
        f"⚠️ <b>Respaldos atrasados</b>\n"
        f"{len(atrasados)} de {total_enabled} equipos sin respaldo en "
        f"{horas} h.\n"
        f"<i>{ecuador:%Y-%m-%d %H:%M} (hora Ecuador)</i>"
    )
    if not atrasados:
        return cabecera

    por_empresa: dict[str, list[dict]] = {}
    for item in atrasados:
        por_empresa.setdefault(item["group"], []).append(item)

    lineas = []
    mostrados = 0
    for empresa in sorted(por_empresa):
        if mostrados >= MAX_LISTED:
            break
        lineas.append(f"\n<b>{empresa}</b>")
        for item in por_empresa[empresa]:
            if mostrados >= MAX_LISTED:
                break
            if item["horas"] is None:
                detalle = "nunca"
            elif item["horas"] >= 48:
                detalle = f"hace {int(item['horas'] // 24)} d"
            else:
                detalle = f"hace {int(item['horas'])} h"
            lineas.append(f"  • {item['name']} ({detalle})")
            mostrados += 1

    restantes = len(atrasados) - mostrados
    if restantes > 0:
        lineas.append(f"\n…y {restantes} equipo(s) más.")
    return cabecera + "\n" + "\n".join(lineas)
