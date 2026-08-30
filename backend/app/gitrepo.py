import asyncio
import datetime as dt
import os
import re


COMMIT_PATTERN = re.compile(r"^[0-9a-f]{6,40}$")


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
        raise GitRepoError("git timeout")
    if process.returncode != 0:
        error = stderr.decode(errors="replace").strip()
        lowered = error.lower()
        if (
            "unknown revision" in lowered
            or "bad revision" in lowered
            or "does not exist" in lowered
            or "exists on disk, but not in" in lowered
        ):
            raise NotFoundInRepoError(error)
        raise GitRepoError(error or "git failed")
    return stdout.decode(errors="replace")


async def list_versions(repo_path: str, node: str, limit: int) -> list[dict]:
    output = await _git(
        repo_path,
        "log",
        f"-{limit}",
        "--format=%H%x09%ct%x09%s",
        "--",
        node,
    )
    versions = []
    for line in output.splitlines():
        commit, timestamp, subject = line.split("\t", 2)
        versions.append(
            {
                "commit": commit,
                "date": dt.datetime.fromtimestamp(
                    int(timestamp), tz=dt.timezone.utc
                ),
                "subject": subject,
            }
        )
    return versions


async def show_diff(repo_path: str, node: str, commit: str) -> str:
    return await _git(
        repo_path,
        "show",
        "--date=format-local:%Y-%m-%d %H:%M:%S",
        "--format=commit %h · %cd",
        commit,
        "--",
        node,
    )


async def show_config(repo_path: str, node: str, commit: str) -> str:
    return await _git(repo_path, "show", f"{commit}:{node}")
