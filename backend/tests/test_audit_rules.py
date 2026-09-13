from app.audit_rules import RULES, audit_config, rule_catalog, risk_level, risk_score


# Router descuidado: valores de fábrica peligrosos, resolutor abierto, SNMP
# público, proxy activo, BGP sin filtros y RouterOS 6.
INSECURE = """# jan/02/2026 03:14:07 by RouterOS 6.48.6
# software id = XXXX-YYYY
#
/system identity
set name=MikroTik
/ip dns
set allow-remote-requests=yes servers=8.8.8.8
/snmp
set enabled=yes
/ip proxy
set enabled=yes port=8080
/ip upnp
set enabled=yes
/ip neighbor discovery-settings
set discover-interface-list=all
/tool bandwidth-server
set enabled=yes
/tool mac-server
set allowed-interface-list=all
/user
add name=admin group=full password="ClaveDelRouter"
/routing bgp peer
add name=upstream remote-address=198.51.100.1 remote-as=64500
"""

# Router endurecido: debería salir sin hallazgos.
HARDENED = """# oct/01/2026 08:00:00 by RouterOS 7.23.2
# software id = AAAA-BBBB
#
/system identity
set name=core-quito-01
/ip service
set telnet disabled=yes
set ftp disabled=yes
set www disabled=yes
set api disabled=yes
set api-ssl disabled=yes
set ssh address=10.99.99.0/24 port=2232
set winbox address=10.99.99.0/24
/ip ssh
set strong-crypto=yes
/ip firewall filter
add action=accept chain=input src-address=10.99.99.0/24
add action=drop chain=input comment="drop everything else"
/ip neighbor discovery-settings
set discover-interface-list=mgmt
/tool mac-server
set allowed-interface-list=mgmt
/system ntp client
set enabled=yes servers=10.99.99.1
/routing bgp connection
add name=upstream remote.address=198.51.100.1 remote.as=64500 \\
    input.filter=bgp-in output.filter-chain=bgp-out
"""


def rule_ids(report: dict) -> set[str]:
    return {finding["rule_id"] for finding in report["findings"]}


def test_hardened_config_has_no_findings() -> None:
    report = audit_config(HARDENED)

    assert report["findings"] == []
    assert report["level"] == "ok"
    assert report["score"] == 0
    assert report["ros_version"] == "7.23.2"
    assert report["rules_evaluated"] == len(RULES)


def test_insecure_config_flags_the_expected_rules() -> None:
    found = rule_ids(audit_config(INSECURE))

    assert "ROS-SEC-007" in found  # resolutor DNS abierto
    assert "ROS-SEC-006" in found  # sin descarte en input
    assert "ROS-SEC-008" in found  # SNMP público
    assert "ROS-SEC-009" in found  # proxy habilitado
    assert "ROS-SEC-010" in found  # UPnP
    assert "ROS-SEC-011" in found  # usuario admin
    assert "ROS-SEC-012" in found  # full sin address
    assert "ROS-SEC-013" in found  # bandwidth-server
    assert "ROS-SEC-014" in found  # mac-server en todas
    assert "ROS-SEC-015" in found  # sin strong-crypto
    assert "ROS-SEC-016" in found  # descubrimiento en todas
    assert "ROS-HYG-001" in found  # identidad de fábrica
    assert "ROS-HYG-002" in found  # sin NTP
    assert "ROS-HYG-003" in found  # RouterOS 6
    assert {"ROS-BGP-001", "ROS-BGP-002"} <= found


def test_default_enabled_plaintext_services_are_flagged_without_stanza() -> None:
    found = rule_ids(audit_config(INSECURE))

    # El export no menciona `/ip service`: telnet, ftp, www y api siguen con el
    # valor de fábrica (habilitados) y deben aparecer igual.
    assert {"ROS-SEC-001", "ROS-SEC-002", "ROS-SEC-003", "ROS-SEC-004"} <= found
    telnet = next(
        f for f in audit_config(INSECURE)["findings"] if f["rule_id"] == "ROS-SEC-001"
    )
    assert "valor de fábrica" in telnet["evidence"][0]["detail"]


def test_explicitly_disabled_service_is_not_flagged() -> None:
    report = audit_config("/ip service\nset telnet disabled=yes\n")

    assert "ROS-SEC-001" not in rule_ids(report)


def test_findings_are_deterministic_and_sorted_by_severity() -> None:
    first = audit_config(INSECURE)
    second = audit_config(INSECURE)

    assert first == second
    severities = [finding["severity"] for finding in first["findings"]]
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    assert severities == sorted(severities, key=lambda item: order[item])


def test_evidence_never_leaks_router_passwords() -> None:
    report = audit_config(INSECURE)

    serialized = repr(report)
    assert "ClaveDelRouter" not in serialized
    assert "password=***" in serialized


def test_open_resolver_is_cleared_by_a_drop_rule() -> None:
    config = (
        "/ip dns\nset allow-remote-requests=yes\n"
        "/ip firewall filter\n"
        "add action=drop chain=input protocol=udp dst-port=53 in-interface-list=WAN\n"
    )

    assert "ROS-SEC-007" not in rule_ids(audit_config(config))


def test_generic_input_drop_also_covers_the_dns_port() -> None:
    config = (
        "/ip dns\nset allow-remote-requests=yes\n"
        "/ip firewall filter\nadd action=drop chain=input\n"
    )

    assert "ROS-SEC-007" not in rule_ids(audit_config(config))


def test_snmp_disabled_is_not_flagged() -> None:
    report = audit_config("/snmp community\nset [ find default=yes ] name=public\n")

    assert "ROS-SEC-008" not in rule_ids(report)


def test_legacy_bgp_peer_with_filters_passes() -> None:
    config = (
        "/routing bgp peer\n"
        "add name=upstream remote-address=198.51.100.1 in-filter=in out-filter=out\n"
    )

    assert not {"ROS-BGP-001", "ROS-BGP-002"} & rule_ids(audit_config(config))


def test_bgp_evidence_names_the_peer() -> None:
    finding = next(
        f for f in audit_config(INSECURE)["findings"] if f["rule_id"] == "ROS-BGP-001"
    )

    assert "198.51.100.1" in finding["evidence"][0]["detail"]


def test_score_and_level_follow_the_worst_finding() -> None:
    assert risk_level({"critical": 1}) == "critical"
    assert risk_level({"low": 3}) == "low"
    assert risk_level({}) == "ok"
    assert risk_score({"low": 2}) == 2
    assert risk_score({"critical": 5}) == 100  # tope


def test_rule_catalog_ids_are_unique_and_documented() -> None:
    catalog = rule_catalog()

    assert len(catalog) == len(RULES)
    assert len({rule["id"] for rule in catalog}) == len(RULES)
    assert all(rule["recommendation"] and rule["title"] for rule in catalog)
    assert {rule["category"] for rule in catalog} == {"security", "hygiene", "bgp"}


def test_empty_config_does_not_crash() -> None:
    report = audit_config("")

    assert report["level"] in {"ok", "low", "medium", "high", "critical"}
    assert report["ros_version"] == ""
