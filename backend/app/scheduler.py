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


async def scheduler_loop(app, settings: Settings) -> None:
    last_run: dict[str, float] = {}
    loop = asyncio.get_running_loop()
    last_push = loop.time()
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
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Fallo en el ciclo de respaldo programado")
