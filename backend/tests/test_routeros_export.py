from app.routeros_export import parse_export, redact_secrets, version_tuple


def test_sections_and_params_are_split() -> None:
    export = parse_export(
        "# oct/01/2026 08:00:00 by RouterOS 7.23.2\n"
        "# software id = ABCD-1234\n"
        "/ip service\n"
        "set telnet disabled=yes\n"
        'set www-ssl address=10.0.0.0/8 certificate="panel cert"\n'
    )

    assert export.version == "7.23.2"
    services = export.section("/ip service")
    assert [stanza.command for stanza in services] == ["set", "set"]
    assert services[0].params == {"disabled": "yes"}
    assert services[0].positional == ("telnet",)
    assert services[1].get("certificate") == "panel cert"


def test_line_continuations_are_joined_and_keep_first_line_number() -> None:
    export = parse_export(
        "/ip firewall filter\n"
        "add action=drop chain=input \\\n"
        '    comment="drop invalid" \\\n'
        "    connection-state=invalid\n"
    )

    stanzas = export.section("/ip firewall filter")
    assert len(stanzas) == 1
    assert stanzas[0].line == 2
    assert stanzas[0].get("connection-state") == "invalid"
    assert stanzas[0].get("comment") == "drop invalid"


def test_find_expressions_are_positional_not_params() -> None:
    export = parse_export("/snmp community\nset [ find default=yes ] name=public\n")

    stanza = export.section("/snmp community")[0]
    assert stanza.get("name") == "public"
    assert stanza.positional == ("[ find default=yes ]",)
    assert "default" not in stanza.params


def test_under_includes_subsections() -> None:
    export = parse_export(
        "/system ntp client\nset enabled=yes\n"
        "/system ntp client servers\nadd address=1.2.3.4\n"
    )

    assert len(export.section("/system ntp client")) == 1
    assert len(export.under("/system ntp client")) == 2


def test_quoted_values_with_spaces_stay_together() -> None:
    export = parse_export('/system identity\nset name="Core Quito 01"\n')

    assert export.section("/system identity")[0].get("name") == "Core Quito 01"


def test_version_tuple_ignores_suffixes() -> None:
    assert version_tuple("7.23.2") == (7, 23, 2)
    assert version_tuple("7.13beta4") == (7, 13)
    assert version_tuple("6.48.6") == (6, 48, 6)
    assert version_tuple("") == ()


def test_redact_secrets_masks_credentials_but_keeps_context() -> None:
    line = 'add name=respaldo group=full password="S3cr3t o" address=10.0.0.0/8'

    assert redact_secrets(line) == (
        "add name=respaldo group=full password=*** address=10.0.0.0/8"
    )


def test_redact_secrets_covers_prefixed_keys() -> None:
    redacted = redact_secrets(
        "set authentication-password=abc wpa2-pre-shared-key=xyz mode=dynamic-keys"
    )

    assert "abc" not in redacted
    assert "xyz" not in redacted
    assert "mode=dynamic-keys" in redacted
