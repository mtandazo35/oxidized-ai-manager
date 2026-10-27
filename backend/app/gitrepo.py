import asyncio
import datetime as dt
import os
import re


COMMIT_PATTERN = re.compile(r"^[0-9a-f]{6,40}$")

# Árbol vacío de git (SHA-1). Git lo conoce aunque no esté en el repositorio:
# sirve para mostrar una versión como "archivo nuevo" sin enseñar la anterior.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

DIFF_HEADER_ARGS = (
    "--date=format-local:%Y-%m-%d %H:%M:%S",
    "--format=commit %h · %cd",
)


class GitRepoError(Exception):
    """The backups repository could not be read."""


class NotFoundInRepoError(Exception):
    """The requested commit or file does not exist in the repository."""


async def _git(repo_path: str, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-c",
        "safe.directory=*",
        "-C",
        repo_path,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "TZ": os.environ.get("APP_TIMEZONE", "America/Guayaquil"),
        },
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    except asyncio.TimeoutError:
        process.kill()
        # Sin esperar al proceso queda un zombi y el transporte sin cerrar.
        await process.wait()
        raise GitRepoError("git timeout")
    if process.returncode != 0:
        error = stderr.decode(errors="replace").strip()
        lowered = error.lower()
        if (
            "unknown revision" in lowered
            or "bad revision" in lowered
            or "does not exist" in lowered
            or "exists on disk, but not in" in lowered
            or "bad object" in lowered
        ):
            raise NotFoundInRepoError(error)
        raise GitRepoError(error or "git failed")
    return stdout.decode(errors="replace")


def _epoch(since: dt.datetime | None) -> int | None:
    """Segundo desde el que se acepta historial (git guarda segundos enteros).

    Se redondea hacia abajo: un respaldo hecho en el mismo segundo del alta
    ya pertenece al equipo nuevo.
    """
    if since is None:
        return None
    if since.tzinfo is None:
        since = since.replace(tzinfo=dt.timezone.utc)
    return int(since.timestamp())


def _parse_log(output: str) -> list[tuple[str, int, str]]:
    entries = []
    for line in output.splitlines():
        if not line.strip():
            continue
        commit, timestamp, subject = (line.split("\t", 2) + [""])[:3]
        entries.append((commit, int(timestamp), subject))
    return entries


async def list_versions(
    repo_path: str, node: str, limit: int, since: dt.datetime | None = None
) -> list[dict]:
    """Versiones del nodo, de la más nueva a la más vieja.

    `since` oculta lo anterior a esa fecha: el nombre del nodo puede haber
    pertenecido antes a otro equipo (incluso de otra empresa) y su historial
    sigue en el repositorio, que es de Oxidized y no se puede purgar.
    """
    minimum = _epoch(since)
    args = ["log", f"-{limit}", "--format=%H%x09%ct%x09%s"]
    if minimum is not None:
        args.append(f"--since=@{minimum}")
    output = await _git(repo_path, *args, "--", node)
    versions = []
    for commit, timestamp, subject in _parse_log(output):
        # `--since` ya filtra; se repite aquí por si git corta mal con relojes
        # desordenados.
        if minimum is not None and timestamp < minimum:
            continue
        versions.append(
            {
                "commit": commit,
                "date": dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc),
                "subject": subject,
            }
        )
    return versions


async def _node_history_at(
    repo_path: str, node: str, commit: str, count: int
) -> list[tuple[str, int, str]]:
    """Las `count` últimas versiones del nodo alcanzables desde `commit`."""
    output = await _git(
        repo_path, "log", f"-{count}", "--format=%H%x09%ct%x09%s", commit, "--", node
    )
    return _parse_log(output)


async def show_diff(
    repo_path: str, node: str, commit: str, since: dt.datetime | None = None
) -> str:
    minimum = _epoch(since)
    if minimum is None:
        return await _git(repo_path, "show", *DIFF_HEADER_ARGS, commit, "--", node)
    own = _parse_log(
        await _git(repo_path, "show", "-s", "--format=%H%x09%ct%x09%s", commit)
    )
    if not own or own[0][1] < minimum:
        # Mismo error que una versión inexistente: no se confirma nada sobre el
        # historial del dueño anterior del nombre.
        raise NotFoundInRepoError("commit anterior al alta del equipo")
    history = await _node_history_at(repo_path, node, commit, 2)
    touches = bool(history) and history[0][0] == own[0][0]
    previous = history[1] if touches and len(history) > 1 else None
    if previous is None or previous[1] >= minimum:
        return await _git(repo_path, "show", *DIFF_HEADER_ARGS, commit, "--", node)
    # La versión anterior es del dueño previo del nombre: se muestra esta
    # como si el archivo naciera aquí, sin las líneas que se quitaron.
    header = await _git(repo_path, "show", "-s", *DIFF_HEADER_ARGS, commit)
    diff = await _git(repo_path, "diff", EMPTY_TREE, commit, "--", node)
    return f"{header.rstrip()}\n\n{diff}"


async def show_config(
    repo_path: str, node: str, commit: str, since: dt.datetime | None = None
) -> str:
    minimum = _epoch(since)
    if minimum is not None:
        # Lo que devuelve `commit:node` es la versión del último commit que
        # tocó el nodo, que puede ser muy anterior a `commit`.
        history = await _node_history_at(repo_path, node, commit, 1)
        if not history or history[0][1] < minimum:
            raise NotFoundInRepoError("versión anterior al alta del equipo")
    return await _git(repo_path, "show", f"{commit}:{node}")
