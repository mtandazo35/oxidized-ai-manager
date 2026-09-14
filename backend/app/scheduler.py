import asyncio
import datetime as dt
import logging
import os
import re

import httpx

from .config import Settings


log = logging.getLogger("oxidized-ai-manager.scheduler")

CREDENTIAL_PATTERN = re.compile(r"(https?://[^/:@]+):[^@]+@")


def mask_remote_url(url: str) -> str:
    return CREDENTIAL_PATTERN.sub(r"\1:***@", url)


async def trigger_node_backup(oxidized_url: str, node: str) -> None:
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.get(f"{oxidized_url.rstrip('/')}/node/next/{node}")
        if response.status_code >= 500:
            raise httpx.HTTPStatusError(
                "oxidized error", request=response.request, response=response
            )




async def reload_nodes(oxidized_url: str) -> None:
    """Hace que Oxidized relea el inventario del backend."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{oxidized_url.rstrip('/')}/reload")
        response.raise_for_status()


async def refresh_inventory(oxidized_url: str) -> None:
    """Hace que Oxidized relea el inventario tras un cambio.

    Oxidized no consulta la base: pide la lista al backend y la guarda en
    memoria. Sin esto, editar la clave de un equipo dejaba a Oxidized
    intentando entrar con la vieja, y borrar uno lo dejaba respaldando un
    equipo que ya no existe.

    Es "mejor esfuerzo": si Oxidized no responde, la operación del usuario no
    debe fallar por ello.
    """
    try:
        await reload_nodes(oxidized_url)
    except httpx.HTTPError as error:
        log.warning("No se pudo recargar el inventario en Oxidized: %s", error)


async def backup_new_nodes(oxidized_url: str, names: list[str]) -> None:
    """Encola el primer respaldo de equipos recién dados de alta.

    Oxidized tiene su `interval` anulado (lo programa el backend), así que sin
    esto un equipo nuevo esperaría un ciclo entero —hasta una hora— antes de
    respaldarse por primera vez.

    Primero hay que recargar el inventario: Oxidized lee los nodos por HTTP y
    hasta que no lo hace, `/node/next/<nombre>` no conoce el equipo. Todo es
    "mejor esfuerzo": si Oxidized no responde, el alta no debe fallar y el
    ciclo programado acabará recogiéndolo igual.
    """
    if not names:
        return
    try:
        await reload_nodes(oxidized_url)
    except httpx.HTTPError as error:
        log.warning("No se pudo recargar el inventario en Oxidized: %s", error)
        return
    for name in names:
        try:
            await trigger_node_backup(oxidized_url, name)
        except httpx.HTTPError as error:
            log.warning("No se pudo encolar el primer respaldo de %s: %s", name, error)


async def push_backups(repo_path: str, remote_url: str) -> tuple[bool, str]:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-c",
        "safe.directory=*",
        "-C",
        repo_path,
        "push",
        "--quiet",
        remote_url,
        "refs/heads/*:refs/heads/*",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
    except asyncio.TimeoutError:
        process.kill()
        return False, "timeout de git push"
    if process.returncode == 0:
        return True, "ok"
    detail = stderr.decode(errors="replace").strip()[-300:] or "git push falló"
    return False, mask_remote_url(detail)


def is_due(elapsed_seconds: float, interval_minutes: int) -> bool:
    return elapsed_seconds >= max(interval_minutes, 5) * 60


async def run_tick(
    app, settings: Settings, last_run: dict[str, float], now: float
) -> bool:
    values = await app.state.settings.get_all()
    try:
        global_minutes = max(int(values["backup_interval_minutes"]), 5)
    except ValueError:
        global_minutes = 60
    devices = await app.state.devices.list_devices()
    active_names = set()
    triggered = False
    for device in devices:
        name = device["name"]
        active_names.add(name)
        if not device["enabled"]:
            last_run.pop(name, None)
            continue
        interval = device.get("backup_interval_minutes") or global_minutes
        if name not in last_run:
            # Recién visto (arranque o alta): Oxidized ya recolecta al cargar
            # el nodo, así que el primer disparo propio espera su intervalo.
            last_run[name] = now
            continue
        if is_due(now - last_run[name], interval):
            last_run[name] = now
            try:
                await trigger_node_backup(settings.oxidized_url, name)
                triggered = True
            except httpx.HTTPError as error:
                log.warning("No se pudo encolar %s: %s", name, error)
    for stale in set(last_run) - active_names:
        last_run.pop(stale, None)
    return triggered


async def push_now(app, settings: Settings, remote_url: str) -> None:
    ok, detail = await push_backups(settings.oxidized_backup_repo, remote_url)
    await app.state.settings.set_many(
        {
            "last_push_ok": "true" if ok else "false",
            "last_push_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "last_push_detail": detail,
        }
    )
    if not ok:
        log.warning("git push al remoto falló: %s", detail)


async def purge_activity(app, settings: Settings) -> int:
    """Recorta la bitácora. Sin esto la tabla crece sin límite."""
    try:
        return await app.state.activity.purge(settings.activity_retention_days)
    except Exception:
        log.warning("No se pudo purgar la bitácora", exc_info=True)
        return 0


async def scheduler_loop(app, settings: Settings) -> None:
    last_run: dict[str, float] = {}
    loop = asyncio.get_running_loop()
    last_push = loop.time()
    last_purge = loop.time() - 24 * 3600  # que purgue en el primer ciclo
    while True:
        try:
            await asyncio.sleep(60)
            await run_tick(app, settings, last_run, loop.time())
            values = await app.state.settings.get_all()
            if values["git_remote_enabled"] == "true" and values["git_remote_url"]:
                try:
                    push_minutes = int(values["git_push_interval_minutes"])
                except ValueError:
                    push_minutes = 60
                now = loop.time()
                if is_due(now - last_push, push_minutes):
                    last_push = now
                    await push_now(app, settings, values["git_remote_url"])
            if loop.time() - last_purge >= 24 * 3600:
                last_purge = loop.time()
                borradas = await purge_activity(app, settings)
                if borradas:
                    log.info("Bitácora: %s entradas purgadas", borradas)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Fallo en el ciclo de respaldo programado")
