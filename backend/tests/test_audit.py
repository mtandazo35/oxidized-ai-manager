import subprocess

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.audit_rules import RULES
from app.config import get_settings


pytestmark = pytest.mark.anyio

INSECURE_EXPORT = """# jan/02/2026 03:14:07 by RouterOS 6.48.6
/system identity
set name=MikroTik
/ip dns
set allow-remote-requests=yes
/user
add name=admin group=full password="ClaveDelRouter"
"""

HARDENED_EXPORT = """# oct/01/2026 08:00:00 by RouterOS 7.23.2
/system identity
set name=core-quito-01
/ip service
set telnet disabled=yes
set ftp disabled=yes
set www disabled=yes
set api disabled=yes
set api-ssl disabled=yes
set ssh address=10.99.99.0/24
set winbox address=10.99.99.0/24
/ip ssh
set strong-crypto=yes
/ip firewall filter
add action=drop chain=input
/system ntp client
set enabled=yes servers=10.99.99.1
"""


def client() -> AsyncClient:
    transport = ASGITransport(app=main_module.app)
    return AsyncClient(transport=transport, base_url="http://test")


def _git(repo: str, *args: str) -> None:
    subprocess.run(
        ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def audit_repo(tmp_path):
    repo = str(tmp_path / "backups")
    subprocess.run(["git", "init", "-q", repo], check=True, capture_output=True)
    (tmp_path / "backups" / "rb-descuidado").write_text(INSECURE_EXPORT)
    (tmp_path / "backups" / "rb-endurecido").write_text(HARDENED_EXPORT)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "primer respaldo")
    original = get_settings().oxidized_backup_repo
    get_settings().oxidized_backup_repo = repo
    yield repo
    get_settings().oxidized_backup_repo = original


async def test_audit_requires_authentication(audit_repo) -> None:
    async with client() as api:
        response = await api.get("/api/audit/node?node=rb-descuidado")

    assert response.status_code == 401


async def test_audit_node_returns_findings_with_evidence(
    auth_headers, audit_repo
) -> None:
    async with client() as api:
        response = await api.get(
            "/api/audit/node?node=rb-descuidado", headers=auth_headers
        )

    assert response.status_code == 200
    report = response.json()
    assert report["node"] == "rb-descuidado"
    assert report["level"] == "critical"
    assert report["ros_version"] == "6.48.6"
    assert report["rules_evaluated"] == len(RULES)
    assert report["counts"]["critical"] == 1
    ids = [finding["rule_id"] for finding in report["findings"]]
    assert ids[0] == "ROS-SEC-007"  # el más grave primero
    assert all(finding["evidence"] for finding in report["findings"])


async def test_audit_node_never_returns_router_passwords(
    auth_headers, audit_repo
) -> None:
    async with client() as api:
        response = await api.get(
            "/api/audit/node?node=rb-descuidado", headers=auth_headers
        )

    assert "ClaveDelRouter" not in response.text


async def test_hardened_node_reports_no_findings(auth_headers, audit_repo) -> None:
    async with client() as api:
        response = await api.get(
            "/api/audit/node?node=rb-endurecido", headers=auth_headers
        )

    assert response.json()["level"] == "ok"
    assert response.json()["findings"] == []


async def test_audit_node_without_backup_returns_404(auth_headers, audit_repo) -> None:
    async with client() as api:
        response = await api.get(
            "/api/audit/node?node=rb-inexistente", headers=auth_headers
        )

    assert response.status_code == 404


async def test_audit_node_rejects_invalid_commit(auth_headers, audit_repo) -> None:
    async with client() as api:
        response = await api.get(
            "/api/audit/node?node=rb-descuidado&commit=../../etc/passwd",
            headers=auth_headers,
        )

    assert response.status_code == 422


async def test_summary_covers_the_inventory_and_sorts_by_risk(
    auth_headers, audit_repo
) -> None:
    async with client() as api:
        for name in ("rb-endurecido", "rb-descuidado", "rb-sin-respaldo"):
            await api.post(
                "/api/devices",
                headers=auth_headers,
                json={"name": name, "address": "192.0.2.1", "group_name": "EmpresaA"},
            )
        response = await api.get("/api/audit/summary", headers=auth_headers)

    assert response.status_code == 200
    rows = {row["node"]: row for row in response.json()}
    assert len(rows) == 3
    assert response.json()[0]["node"] == "rb-descuidado"  # peor puntaje primero
    assert rows["rb-descuidado"]["level"] == "critical"
    assert rows["rb-endurecido"]["level"] == "ok"
    assert rows["rb-sin-respaldo"]["level"] == "unknown"
    assert rows["rb-sin-respaldo"]["error"] == "sin respaldo todavía"
    assert rows["rb-descuidado"]["group_name"] == "EmpresaA"


async def test_rule_catalog_is_served_for_the_panel(auth_headers) -> None:
    async with client() as api:
        response = await api.get("/api/audit/rules", headers=auth_headers)

    assert response.status_code == 200
    assert len(response.json()["rules"]) == len(RULES)
