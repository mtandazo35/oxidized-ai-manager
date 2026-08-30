import subprocess

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.config import get_settings
from app.metadata import parse_routeros_metadata


pytestmark = pytest.mark.anyio

TOKEN_HEADER = {"X-Oxidized-Token": "test-oxidized-token"}

ROUTEROS_EXPORT = """\
#                   version: 7.23.2 (stable)
#              total-memory: 2048.0MiB
#                board-name: CHR QEMU Standard PC
#                      name: MikroTik
/ip address
add address=192.0.2.1/24 interface=ether1
/system identity
set name="CORE-QUEVEDO"
"""


def test_parse_extracts_version_board_and_identity() -> None:
    meta = parse_routeros_metadata(ROUTEROS_EXPORT)

    assert meta["ros_version"] == "7.23.2 (stable)"
    assert meta["board"] == "CHR QEMU Standard PC"
    assert meta["identity"] == "CORE-QUEVEDO"


def test_parse_identity_from_comment_when_no_export_block() -> None:
    meta = parse_routeros_metadata("#     name: rb-sucursal\n/ip address\n")

    assert meta["identity"] == "rb-sucursal"


def test_parse_empty_config() -> None:
    assert parse_routeros_metadata("") == {}


@pytest.fixture
def metadata_repo(tmp_path):
    repo = str(tmp_path / "backups")
    subprocess.run(["git", "init", "-q", repo], check=True, capture_output=True)
    (tmp_path / "backups" / "rb-lab-01").write_text(ROUTEROS_EXPORT)
    subprocess.run(
        ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t",
         "add", "rb-lab-01"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "-m", "update /rb-lab-01"],
        check=True, capture_output=True,
    )
    original = get_settings().oxidized_backup_repo
    get_settings().oxidized_backup_repo = repo
    yield repo
    get_settings().oxidized_backup_repo = original


async def test_success_event_updates_device_metadata(
    auth_headers, metadata_repo
) -> None:
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        await api.post(
            "/api/devices",
            headers=auth_headers,
            json={"name": "rb-lab-01", "address": "192.0.2.1"},
        )
        await api.post(
            "/api/oxidized/events",
            headers=TOKEN_HEADER,
            json={"node": "rb-lab-01", "event": "node_success"},
        )
        listed = await api.get("/api/devices", headers=auth_headers)

    device = listed.json()[0]
    assert device["identity"] == "CORE-QUEVEDO"
    assert device["ros_version"] == "7.23.2 (stable)"
    assert device["board"] == "CHR QEMU Standard PC"
