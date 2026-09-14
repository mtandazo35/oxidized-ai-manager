"""Actualización desde Git, pedida desde el panel y ejecutada en el anfitrión.

Por qué este rodeo en lugar de que el backend haga `docker compose up`:
reconstruir el stack exige el socket de Docker, y montarlo dentro del
contenedor equivale a darle **root del anfitrión** a la aplicación que está
publicada tras el proxy. Así que aquí no se ejecuta nada: el backend solo
**deja una petición** en un directorio compartido, y una unidad de systemd del
anfitrión (`oxidized-ai-manager-updater.path`) la recoge y aplica.

La petición **no lleva ni URL ni rama ni commit**: el script del anfitrión
tiene fijado el remoto y hace `git pull --ff-only origin main`. Así, incluso
con la cuenta de administrador comprometida, lo único que se consigue es
desplegar el último commit legítimo del repositorio de siempre, no código
arbitrario.

El repositorio se monta de **solo lectura** en `/repo/.git` para poder leer en
qué commit está y compararlo con el remoto.
"""

import asyncio
import datetime as dt
import json
import os
import re
from pathlib import Path

import httpx


REQUEST_FILE = "update-request.json"
STATUS_FILE = "update-status.json"

# Estados que escribe el script del anfitrión.
RUNNING_STATES = ("solicitada", "en-progreso")


class UpdaterError(Exception):
    """No se pudo consultar o solicitar la actualización."""


async def _git(*args: str, timeout: int = 30) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-c",
        "safe.directory=*",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        raise UpdaterError("git tardó demasiado")
    if process.returncode != 0:
        raise UpdaterError(stderr.decode(errors="replace").strip()[-300:] or "git falló")
    return stdout.decode(errors="replace").strip()


async def local_commit(git_dir: str) -> dict:
    """Commit desplegado, con su fecha y asunto."""
    output = await _git(
        "--git-dir", git_dir, "log", "-1", "--format=%H%x09%ct%x09%s"
    )
    commit, timestamp, subject = output.split("\t", 2)
    return {
        "commit": commit,
        "short": commit[:7],
        "date": dt.datetime.fromtimestamp(int(timestamp), tz=dt.timezone.utc),
        "subject": subject,
    }


async def remote_commit(git_dir: str, branch: str = "main") -> str:
    """Commit en la punta de la rama remota, sin traer nada al repositorio.

    `ls-remote` solo consulta: no escribe en el repositorio (que además está
    montado de solo lectura).
    """
    url = await _git("--git-dir", git_dir, "config", "--get", "remote.origin.url")
    output = await _git("ls-remote", url, f"refs/heads/{branch}", timeout=25)
    if not output:
        raise UpdaterError(f"el remoto no tiene la rama {branch}")
    return output.split()[0]


def read_status(channel_dir: str) -> dict:
    """Última actualización conocida, según lo que dejó el anfitrión."""
    path = Path(channel_dir) / STATUS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "sin-datos", "detail": "", "at": None}
    if not isinstance(data, dict):
        return {"state": "sin-datos", "detail": "", "at": None}
    return {
        "state": str(data.get("state", "sin-datos"))[:40],
        "detail": str(data.get("detail", ""))[:2000],
        "at": data.get("at"),
        "from": str(data.get("from", ""))[:40],
        "to": str(data.get("to", ""))[:40],
    }


def is_running(channel_dir: str) -> bool:
    return read_status(channel_dir).get("state") in RUNNING_STATES


def request_update(channel_dir: str, username: str) -> dict:
    """Deja la petición para el anfitrión y marca el estado como solicitada.

    Deliberadamente no acepta rama, commit ni URL: el script del anfitrión los
    tiene fijados.
    """
    directory = Path(channel_dir)
    if not directory.is_dir():
        raise UpdaterError(
            "El canal de actualización no está montado: falta el volumen "
            f"{channel_dir} y la unidad del anfitrión."
        )
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    payload = {"requested_by": username, "at": now}
    try:
        # El estado se escribe ANTES de la petición: así, si systemd dispara al
        # instante, el panel nunca ve un hueco sin estado.
        (directory / STATUS_FILE).write_text(
            json.dumps({"state": "solicitada", "detail": "", "at": now}),
            encoding="utf-8",
        )
        (directory / REQUEST_FILE).write_text(
            json.dumps(payload), encoding="utf-8"
        )
    except OSError as error:
        raise UpdaterError(f"no se pudo escribir la petición: {error}")
    return payload


# Un remoto de GitHub, en cualquiera de sus dos formas habituales.
_GITHUB = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)(?P<repo>[^/]+/[^/]+?)(?:\.git)?$"
)


async def pending_changes(git_dir: str, base: str, head: str) -> list[dict]:
    """Los commits que trae la actualización, del más nuevo al más viejo.

    El repositorio está montado de solo lectura, así que no se puede hacer
    `git fetch` para leer los mensajes: se piden a la API pública de GitHub,
    que para un repositorio público no necesita credenciales. Si el remoto no
    es de GitHub o la consulta falla, se devuelve una lista vacía: saber qué
    trae la actualización está bien, pero no es motivo para romper la pantalla
    de versión.
    """
    if not base or not head or base == head:
        return []
    try:
        url = await _git("--git-dir", git_dir, "config", "--get", "remote.origin.url")
    except UpdaterError:
        return []
    match = _GITHUB.match(url.strip())
    if not match:
        return []
    api = f"https://api.github.com/repos/{match.group('repo')}/compare/{base}...{head}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                api, headers={"Accept": "application/vnd.github+json"}
            )
        response.raise_for_status()
        commits = response.json().get("commits") or []
    except (httpx.HTTPError, ValueError):
        return []
    cambios = [
        {
            "short": str(c.get("sha", ""))[:7],
            # Solo el asunto: el cuerpo del mensaje explica el porqué y aquí
            # sobra, la pantalla es una lista.
            "subject": (str((c.get("commit") or {}).get("message", "")).splitlines() or [""])[0][:150],
            "date": ((c.get("commit") or {}).get("author") or {}).get("date", ""),
        }
        for c in commits
        if c.get("sha")
    ]
    cambios.reverse()
    return cambios[:30]
