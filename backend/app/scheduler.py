import asyncio
import datetime as dt
import logging
import os
import re

import httpx

from .config import Settings


log = logging.getLogger("oxidized-ai-manager.scheduler")

# Oxidized queues the collection asynchronously; wait before pushing so the
# resulting commits are included in the same cycle.
PUSH_DELAY_SECONDS = 120
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


async def trigger_all_backups(devices_repository, oxidized_url: str) -> None:
    nodes = await devices_repository.list_oxidized_nodes()
    for node in nodes:
        try:
            await trigger_node_backup(oxidized_url, node["name"])
        except httpx.HTTPError as error:
            log.warning("No se pudo encolar %s: %s", node["name"], error)


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


async def run_cycle(app, settings: Settings) -> None:
    await trigger_all_backups(app.state.devices, settings.oxidized_url)
    values = await app.state.settings.get_all()
    if values["git_remote_enabled"] == "true" and values["git_remote_url"]:
        await asyncio.sleep(PUSH_DELAY_SECONDS)
        ok, detail = await push_backups(
            settings.oxidized_backup_repo, values["git_remote_url"]
        )
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
    while True:
        try:
            values = await app.state.settings.get_all()
            minutes = max(int(values["backup_interval_minutes"]), 5)
        except Exception:
            minutes = 60
        try:
            await asyncio.sleep(minutes * 60)
            await run_cycle(app, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Fallo en el ciclo de respaldo programado")
