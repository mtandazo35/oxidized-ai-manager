import subprocess

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.config import get_settings


pytestmark = pytest.mark.anyio


def git(repo: str, *args: str) -> None:
    subprocess.run(
        ["git", "-C", repo, "-c", "user.name=test", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def backup_repo(tmp_path):
    repo = str(tmp_path / "backups")
    subprocess.run(["git", "init", "-q", repo], check=True, capture_output=True)
    node_file = tmp_path / "backups" / "rb-lab-01"
    node_file.write_text("/ip address add address=192.0.2.1\n")
    git(repo, "add", "rb-lab-01")
    git(repo, "commit", "-q", "-m", "update /rb-lab-01")
    node_file.write_text(
        "/ip address add address=192.0.2.1\n/ip service set ssh port=2222\n"
    )
    git(repo, "add", "rb-lab-01")
    git(repo, "commit", "-q", "-m", "update /rb-lab-01")
    original = get_settings().oxidized_backup_repo
    get_settings().oxidized_backup_repo = repo
    yield repo
    get_settings().oxidized_backup_repo = original


def client(auth_headers: dict[str, str]) -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers
    )


async def test_versions_require_login(backup_repo) -> None:
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.get("/api/backups/versions", params={"node": "rb-lab-01"})

    assert response.status_code == 401


async def test_versions_listed_newest_first(auth_headers, backup_repo) -> None:
    async with client(auth_headers) as api:
        response = await api.get("/api/backups/versions", params={"node": "rb-lab-01"})

    assert response.status_code == 200
    versions = response.json()
    assert len(versions) == 2
    assert versions[0]["subject"] == "update /rb-lab-01"


async def test_versions_unknown_node_returns_empty(auth_headers, backup_repo) -> None:
    async with client(auth_headers) as api:
        response = await api.get("/api/backups/versions", params={"node": "no-existe"})

    assert response.status_code == 200
    assert response.json() == []


async def test_diff_shows_change(auth_headers, backup_repo) -> None:
    async with client(auth_headers) as api:
        versions = (await api.get(
            "/api/backups/versions", params={"node": "rb-lab-01"}
        )).json()
        response = await api.get(
            "/api/backups/diff",
            params={"node": "rb-lab-01", "commit": versions[0]["commit"]},
        )

    assert response.status_code == 200
    assert "+/ip service set ssh port=2222" in response.json()["diff"]


async def test_config_returns_full_version(auth_headers, backup_repo) -> None:
    async with client(auth_headers) as api:
        versions = (await api.get(
            "/api/backups/versions", params={"node": "rb-lab-01"}
        )).json()
        oldest = versions[-1]["commit"]
        response = await api.get(
            "/api/backups/config",
            params={"node": "rb-lab-01", "commit": oldest},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["config"] == "/ip address add address=192.0.2.1\n"


async def test_diff_unknown_commit_returns_404(auth_headers, backup_repo) -> None:
    async with client(auth_headers) as api:
        response = await api.get(
            "/api/backups/diff",
            params={"node": "rb-lab-01", "commit": "deadbeef"},
        )

    assert response.status_code == 404


async def test_bad_commit_format_rejected(auth_headers, backup_repo) -> None:
    async with client(auth_headers) as api:
        response = await api.get(
            "/api/backups/diff",
            params={"node": "rb-lab-01", "commit": "no;valido"},
        )

    assert response.status_code == 422


async def test_a_git_timeout_reaps_the_process(monkeypatch) -> None:
    """Tras `kill()` hay que esperar al proceso: si no, queda un zombi."""
    import asyncio

    from app import gitrepo

    class ProcesoColgado:
        returncode = None

        def __init__(self) -> None:
            self.killed = False
            self.waited = False

        async def communicate(self):
            await asyncio.sleep(3600)

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.waited = True
            return -9

    proceso = ProcesoColgado()

    async def fake_exec(*args, **kwargs):
        return proceso

    async def fake_wait_for(awaitable, timeout):
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(gitrepo.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(gitrepo.asyncio, "wait_for", fake_wait_for)

    with pytest.raises(gitrepo.GitRepoError):
        await gitrepo._git("/repo", "log")

    assert proceso.killed and proceso.waited


@pytest.mark.parametrize(
    "path",
    [
        "/api/backups/versions?node=rb-lab-01",
        "/api/backups/diff?node=rb-lab-01&commit=abc123",
        "/api/backups/config?node=rb-lab-01&commit=abc123",
    ],
)
async def test_git_errors_do_not_leak_server_paths(
    auth_headers, monkeypatch, path
) -> None:
    from app import gitrepo

    async def fallo(*args, **kwargs):
        raise gitrepo.GitRepoError(
            "fatal: not a git repository: '/var/lib/oxidized/backups.git'"
        )

    monkeypatch.setattr(gitrepo, "_git", fallo)

    async with client(auth_headers) as api:
        response = await api.get(path)

    assert response.status_code == 502
    assert response.json()["detail"] == "No se pudo leer el repositorio de respaldos."
    assert "/var/lib" not in response.text


async def test_versions_before_history_since_are_hidden(backup_repo) -> None:
    """Llamada directa: lo anterior a `since` no sale, ni por versión ni por HEAD."""
    import datetime as dt

    from app import gitrepo

    futuro = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
    pasado = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)

    assert await gitrepo.list_versions(backup_repo, "rb-lab-01", 10, since=futuro) == []
    assert len(await gitrepo.list_versions(backup_repo, "rb-lab-01", 10, since=pasado)) == 2
    with pytest.raises(gitrepo.NotFoundInRepoError):
        await gitrepo.show_config(backup_repo, "rb-lab-01", "HEAD", since=futuro)
    with pytest.raises(gitrepo.NotFoundInRepoError):
        await gitrepo.show_diff(backup_repo, "rb-lab-01", "HEAD", since=futuro)
    assert "ssh port=2222" in await gitrepo.show_config(
        backup_repo, "rb-lab-01", "HEAD", since=pasado
    )
