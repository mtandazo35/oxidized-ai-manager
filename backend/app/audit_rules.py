"""Reglas de auditoría sobre configuraciones RouterOS ya respaldadas.

Motor **determinista y read-only**: la entrada es el texto de un respaldo de
Oxidized y la salida son hallazgos con evidencia. La misma configuración
produce siempre los mismos hallazgos, que es el criterio de aceptación de la
Fase 4. Aquí no hay red, ni base de datos, ni LLM: nada de esto toca un router.

Las reglas se agrupan por agente lógico del roadmap:

- `security` — Security Agent: exposición de servicios y riesgos de acceso.
- `hygiene` — Audit Agent: desviaciones de estándares internos.
- `bgp`      — BGP Agent: consistencia de políticas de enrutamiento.

Recordatorio sobre `/export`: solo imprime lo que difiere del valor
predeterminado. Cuando una regla depende de un valor por defecto peligroso
(telnet, FTP, HTTP y API vienen habilitados de fábrica) lo dice en su
evidencia, para que el hallazgo sea auditable y no parezca magia.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass

from .routeros_export import (
    Export,
    Stanza,
    parse_export,
    redact_secrets,
    version_tuple,
)


CRITICAL = "critical"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"

SEVERITY_ORDER = (CRITICAL, HIGH, MEDIUM, LOW)
SEVERITY_WEIGHT = {CRITICAL: 40, HIGH: 15, MEDIUM: 5, LOW: 1}

# Versión mínima que consideramos soportada para RouterOS 7.
MIN_SUPPORTED_VERSION = (7, 12)
# La rama 6 long-term sigue con mantenimiento: por debajo de esto sí preocupa.
MIN_V6_LONG_TERM = (6, 49)

# Servicios que RouterOS habilita de fábrica y transportan credenciales en
# claro (o exponen una superficie de gestión innecesaria).
PLAINTEXT_SERVICES = {
    "telnet": (HIGH, "Telnet transmite la contraseña en texto plano."),
    "ftp": (MEDIUM, "FTP transmite la contraseña en texto plano."),
    "www": (MEDIUM, "WebFig por HTTP transmite la sesión sin cifrar."),
    "api": (HIGH, "La API en texto plano (8728) expone credenciales."),
}
DEFAULT_ENABLED_SERVICES = frozenset(PLAINTEXT_SERVICES) | {
    "ssh",
    "winbox",
    "api-ssl",
}
RESTRICTABLE_SERVICES = ("ssh", "winbox", "api-ssl", "www-ssl")


@dataclass(frozen=True)
class Evidence:
    detail: str
    line: int = 0


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    severity: str
    title: str
    recommendation: str
    check: Callable[[Export], list[Evidence]]
    # Permite ajustar la severidad según el resto de la configuración. Sin
    # esto, un servicio protegido por el firewall pesaba igual que uno abierto
    # a Internet.
    severity_of: Callable[[Export], str] | None = None


def _lower_if_firewalled(base: str, protegido: str = LOW):
    """Baja la severidad cuando la cadena `input` está cerrada."""

    def calcular(export: Export) -> str:
        return protegido if _input_chain_is_closed(export) else base

    return calcular


def _service_state(export: Export) -> dict[str, Stanza | None]:
    """Estado explícito de `/ip service` indexado por nombre de servicio."""
    state: dict[str, Stanza | None] = {}
    for stanza in export.section("/ip service"):
        name = stanza.get("name") or (
            stanza.positional[0] if stanza.positional else ""
        )
        if not name.startswith("["):
            state[name.lower()] = stanza
    return state


def _service_is_enabled(export: Export, service: str) -> tuple[bool, Stanza | None]:
    """(habilitado, stanza explícita). Modela el valor de fábrica si no hay stanza."""
    stanza = _service_state(export).get(service)
    if stanza is None:
        return service in DEFAULT_ENABLED_SERVICES, None
    if stanza.is_true("disabled"):
        return False, stanza
    return True, stanza


def _input_filters(export: Export) -> list[Stanza]:
    return [
        stanza
        for stanza in export.section("/ip firewall filter")
        if stanza.command == "add" and stanza.get("chain") == "input"
    ]


# Condiciones que estrechan una regla: si aparece alguna, el descarte NO cubre
# todo lo demás. `connection-state=invalid` es el ejemplo típico: descarta
# basura, no cierra la cadena.
NARROWING = (
    "dst-port",
    "connection-state",
    "src-address",
    "src-address-list",
    "dst-address",
    "dst-address-list",
    "content",
    "layer7-protocol",
    "tls-host",
    "p2p",
)


def _is_catch_all_drop(stanza: Stanza, protocol: str = "") -> bool:
    """¿Esta regla descarta todo lo que llegue a la cadena?

    Se admite acotar por interfaz (`in-interface`, `in-interface-list=WAN`):
    para juzgar si un servicio queda expuesto a Internet, un descarte en la
    WAN cuenta igual que uno global.
    """
    if stanza.get("action") not in ("drop", "reject"):
        return False
    if any(clave in stanza.params for clave in NARROWING):
        return False
    return stanza.get("protocol") in ("", protocol) or not protocol


def _drops_input_port(export: Export, port: str, protocol: str) -> bool:
    """¿El firewall descarta el tráfico entrante a ese puerto?"""
    for stanza in _input_filters(export):
        if _is_catch_all_drop(stanza, protocol):
            return True
        if stanza.get("action") not in ("drop", "reject"):
            continue
        ports = stanza.get("dst-port")
        if not ports:
            continue
        if stanza.get("protocol") not in ("", protocol):
            continue
        if port in {chunk.strip() for chunk in ports.replace("-", ",").split(",")}:
            return True
    return False


def _input_chain_is_closed(export: Export) -> Stanza | None:
    """Devuelve el descarte final de la cadena `input`, si lo hay.

    Una cadena que termina en `add action=drop chain=input` sin condiciones
    solo deja pasar lo permitido explícitamente antes. Con eso, tener un
    servicio habilitado no significa tenerlo expuesto, y señalarlo como si lo
    estuviera es un falso positivo.

    El orden importa: si después del descarte hubiera un `accept`, la cadena no
    estaría cerrada de verdad.
    """
    reglas = [st for st in export.section("/ip firewall filter")
              if st.command == "add" and st.get("chain") == "input"
              and not st.is_true("disabled")]
    for indice, stanza in enumerate(reglas):
        if not _is_catch_all_drop(stanza):
            continue
        if any(posterior.get("action") == "accept" for posterior in reglas[indice + 1:]):
            continue
        return stanza
    return None


# --- Security Agent ---------------------------------------------------------


def _make_plaintext_service_check(service: str) -> Callable[[Export], list[Evidence]]:
    def check(export: Export) -> list[Evidence]:
        enabled, stanza = _service_is_enabled(export, service)
        if not enabled:
            return []
        if stanza is None:
            return [
                Evidence(
                    f"`/ip service` no deshabilita `{service}`: queda con el valor "
                    "de fábrica (habilitado)."
                )
            ]
        detalle = stanza.raw
        if stanza.get("address"):
            # Sigue viajando en claro, pero solo lo alcanzan las redes
            # indicadas: no es lo mismo que tenerlo abierto a Internet.
            detalle += (
                f"  ← limitado a `{stanza.get('address')}`, pero el tráfico "
                "sigue sin cifrar."
            )
        return [Evidence(detalle, stanza.line)] + _nota_firewall(export)

    return check


def _nota_firewall(export: Export) -> list[Evidence]:
    """Deja constancia de que el firewall ya protege el servicio."""
    cierre = _input_chain_is_closed(export)
    if cierre is None:
        return []
    return [
        Evidence(
            "La cadena `input` termina en un descarte general, así que el "
            "servicio no es alcanzable desde fuera de lo permitido; queda "
            "como aviso de higiene, no de exposición.",
            cierre.line,
        )
    ]


def _restricted_plaintext_service(export: Export, service: str) -> bool:
    """¿El servicio está habilitado pero limitado por `address=`?"""
    enabled, stanza = _service_is_enabled(export, service)
    return bool(enabled and stanza is not None and stanza.get("address"))


def _check_services_without_address(export: Export) -> list[Evidence]:
    evidence = []
    for service in RESTRICTABLE_SERVICES:
        enabled, stanza = _service_is_enabled(export, service)
        if not enabled:
            continue
        if stanza is not None and stanza.get("address"):
            continue
        detail = (
            f"`{service}` accesible desde cualquier origen (sin `address=`)."
            if stanza is None
            else stanza.raw
        )
        evidence.append(Evidence(detail, stanza.line if stanza else 0))
    if evidence:
        evidence += _nota_firewall(export)
    return evidence


def _check_input_drop(export: Export) -> list[Evidence]:
    if not export.has_section("/ip firewall filter"):
        return [
            Evidence(
                "El respaldo no contiene ninguna regla en `/ip firewall filter`: "
                "la cadena `input` acepta todo."
            )
        ]
    drops = [
        stanza
        for stanza in _input_filters(export)
        if stanza.get("action") in ("drop", "reject")
    ]
    if drops:
        return []
    return [
        Evidence(
            "Hay reglas en `chain=input` pero ninguna con `action=drop` o "
            "`action=reject`: no existe descarte final."
        )
    ]


def _check_open_resolver(export: Export) -> list[Evidence]:
    dns = [
        stanza
        for stanza in export.section("/ip dns")
        if stanza.is_true("allow-remote-requests")
    ]
    if not dns:
        return []
    if _drops_input_port(export, "53", "udp"):
        return []
    return [
        Evidence(stanza.raw, stanza.line) for stanza in dns
    ] + [
        Evidence(
            "Ninguna regla de `chain=input` descarta udp/53: el router responde "
            "consultas recursivas a Internet (amplificación DNS)."
        )
    ]


def _check_snmp_public(export: Export) -> list[Evidence]:
    enabled = any(stanza.is_true("enabled") for stanza in export.section("/snmp"))
    if not enabled:
        return []
    communities = export.section("/snmp community")
    public = [
        stanza for stanza in communities if stanza.get("name").lower() == "public"
    ]
    if public:
        return [Evidence(stanza.raw, stanza.line) for stanza in public]
    if not communities:
        return [
            Evidence(
                "SNMP habilitado y el respaldo no redefine ninguna comunidad: "
                "sigue activa la comunidad de fábrica `public`."
            )
        ]
    return []


def _check_proxy_services(export: Export) -> list[Evidence]:
    evidence = []
    for section in ("/ip proxy", "/ip socks"):
        for stanza in export.section(section):
            if stanza.is_true("enabled"):
                evidence.append(Evidence(f"{section}: {stanza.raw}", stanza.line))
    return evidence


def _check_upnp(export: Export) -> list[Evidence]:
    return [
        Evidence(stanza.raw, stanza.line)
        for stanza in export.section("/ip upnp")
        if stanza.is_true("enabled")
    ]


def _check_admin_user(export: Export) -> list[Evidence]:
    evidence = []
    for stanza in export.section("/user"):
        if stanza.get("name").lower() != "admin" or stanza.is_true("disabled"):
            continue
        evidence.append(Evidence(stanza.raw, stanza.line))
    return evidence


def _check_full_user_without_address(export: Export) -> list[Evidence]:
    evidence = []
    for stanza in export.section("/user"):
        if stanza.get("group") != "full" or stanza.is_true("disabled"):
            continue
        if stanza.get("address"):
            continue
        evidence.append(
            Evidence(
                f"Usuario `{stanza.get('name') or '?'}` con grupo `full` sin "
                "`address=` que limite el origen.",
                stanza.line,
            )
        )
    return evidence


def _check_bandwidth_server(export: Export) -> list[Evidence]:
    return [
        Evidence(stanza.raw, stanza.line)
        for stanza in export.section("/tool bandwidth-server")
        if stanza.is_true("enabled")
    ]


def _check_mac_server(export: Export) -> list[Evidence]:
    evidence = []
    for section, label in (
        ("/tool mac-server", "MAC-Telnet"),
        ("/tool mac-server mac-winbox", "MAC-Winbox"),
    ):
        for stanza in export.section(section):
            if stanza.get("allowed-interface-list") in ("all", "*"):
                evidence.append(Evidence(f"{label}: {stanza.raw}", stanza.line))
    return evidence


def _check_ssh_strong_crypto(export: Export) -> list[Evidence]:
    for stanza in export.section("/ip ssh"):
        if stanza.is_true("strong-crypto"):
            return []
        if stanza.is_false("strong-crypto"):
            return [Evidence(stanza.raw, stanza.line)]
    return [
        Evidence(
            "`/ip ssh` no activa `strong-crypto`: se aceptan cifrados y MAC "
            "heredados."
        )
    ]


def _check_neighbor_discovery(export: Export) -> list[Evidence]:
    return [
        Evidence(stanza.raw, stanza.line)
        for stanza in export.section("/ip neighbor discovery-settings")
        if stanza.get("discover-interface-list") in ("all", "*")
    ]


# --- Audit Agent (higiene) --------------------------------------------------


def _check_default_identity(export: Export) -> list[Evidence]:
    return [
        Evidence(stanza.raw, stanza.line)
        for stanza in export.section("/system identity")
        if stanza.get("name").lower() == "mikrotik"
    ]


def _check_ntp_client(export: Export) -> list[Evidence]:
    for stanza in export.under("/system ntp client"):
        if stanza.is_true("enabled") or stanza.get("servers"):
            return []
    return [
        Evidence(
            "Sin cliente NTP configurado: las marcas de tiempo de los registros "
            "y de los propios respaldos no son confiables."
        )
    ]


def _check_routeros_version(export: Export) -> list[Evidence]:
    """Solo avisa de versiones realmente atrasadas.

    La rama 6 long-term (6.49.x) sigue recibiendo mantenimiento de MikroTik:
    tratarla como «fuera de soporte» sería un aviso grave donde no lo hay. Lo
    que sí merece aviso es una 6 anterior a la long-term, o una 7 vieja.
    """
    if not export.version:
        return []
    parsed = version_tuple(export.version)
    if not parsed:
        return []
    if parsed[0] <= 6:
        if parsed >= MIN_V6_LONG_TERM:
            return []
        return [
            Evidence(
                f"RouterOS {export.version}: anterior a la rama long-term "
                f"{'.'.join(str(p) for p in MIN_V6_LONG_TERM)}, que acumula "
                "vulnerabilidades ya corregidas."
            )
        ]
    if parsed >= MIN_SUPPORTED_VERSION:
        return []
    return [
        Evidence(
            f"RouterOS {export.version}; mínimo recomendado "
            f"{'.'.join(str(part) for part in MIN_SUPPORTED_VERSION)}."
        )
    ]


# --- BGP Agent --------------------------------------------------------------


def _bgp_sessions(export: Export) -> list[Stanza]:
    sessions = [
        stanza
        for stanza in export.section("/routing bgp connection")
        if stanza.command == "add"
    ]
    sessions += [
        stanza
        for stanza in export.section("/routing bgp peer")
        if stanza.command == "add"
    ]
    return sessions


def _has_filter(stanza: Stanza, direction: str) -> bool:
    legacy = "in-filter" if direction == "input" else "out-filter"
    if stanza.get(legacy):
        return True
    return any(
        key.startswith(f"{direction}.filter") and value
        for key, value in stanza.params.items()
    )


# Rangos que indican infraestructura propia. Se enumeran a mano en lugar de
# usar `is_private` porque esa propiedad de Python incluye también los rangos
# de documentación (192.0.2.0/24, 198.51.100.0/24...), que son justo los que se
# usan para representar un tránsito de ejemplo.
INTERNAL_RANGES = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",   # CGNAT
    "169.254.0.0/16",
    "127.0.0.0/8",
    "fc00::/7",
    "fe80::/10",
)


def _is_internal_peer(address: str) -> bool:
    """¿El vecino BGP está en un rango de infraestructura propia?

    Una sesión contra 172.16.x.x o 10.x.x.x es la red del propio operador
    (iBGP, el enlace con la matriz). Exigirle filtros como a un tránsito de
    Internet genera un aviso grave donde no hay un riesgo equivalente.
    """
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError:
        return False
    return any(
        ip in ipaddress.ip_network(rango)
        for rango in INTERNAL_RANGES
        if ipaddress.ip_network(rango).version == ip.version
    )


def _peer_address(stanza: Stanza) -> str:
    return (
        stanza.get("remote.address")
        or stanza.get("remote-address")
        or stanza.get("name")
        or "?"
    )


def _make_bgp_filter_check(direction: str) -> Callable[[Export], list[Evidence]]:
    def check(export: Export) -> list[Evidence]:
        evidence = []
        for stanza in _bgp_sessions(export):
            if _has_filter(stanza, direction):
                continue
            peer = _peer_address(stanza)
            if _is_internal_peer(peer):
                # Se informa aparte, con severidad baja: ver ROS-BGP-003.
                continue
            evidence.append(
                Evidence(f"Sesión BGP `{peer}` sin filtro de {direction}.", stanza.line)
            )
        return evidence

    return check


def _check_internal_bgp_without_filters(export: Export) -> list[Evidence]:
    evidence = []
    for stanza in _bgp_sessions(export):
        peer = _peer_address(stanza)
        if not _is_internal_peer(peer):
            continue
        if _has_filter(stanza, "input") and _has_filter(stanza, "output"):
            continue
        evidence.append(
            Evidence(
                f"Sesión interna con `{peer}` sin filtros. Es infraestructura "
                "propia, así que el riesgo es bajo, pero un error de "
                "redistribución en el otro extremo se propaga sin freno.",
                stanza.line,
            )
        )
    return evidence


RULES: tuple[Rule, ...] = (
    *(
        Rule(
            id=f"ROS-SEC-{index:03d}",
            category="security",
            severity=PLAINTEXT_SERVICES[service][0],
            title=f"Servicio `{service}` habilitado",
            recommendation=(
                f"{PLAINTEXT_SERVICES[service][1]} Deshabilítelo con "
                f"`/ip service disable {service}`."
            ),
            check=_make_plaintext_service_check(service),
            severity_of=_lower_if_firewalled(PLAINTEXT_SERVICES[service][0]),
        )
        for index, service in enumerate(PLAINTEXT_SERVICES, start=1)
    ),
    Rule(
        id="ROS-SEC-005",
        category="security",
        severity=MEDIUM,
        title="Servicios de gestión sin lista de origen",
        recommendation=(
            "Limite el origen con `/ip service set <servicio> "
            "address=<prefijos de gestión>`, o bloquee el puerto en `chain=input`."
        ),
        check=_check_services_without_address,
        severity_of=_lower_if_firewalled(MEDIUM),
    ),
    Rule(
        id="ROS-SEC-006",
        category="security",
        severity=HIGH,
        title="Cadena `input` sin regla de descarte",
        recommendation=(
            "Cierre la cadena con `/ip firewall filter add chain=input "
            "action=drop` tras permitir explícitamente lo necesario."
        ),
        check=_check_input_drop,
    ),
    Rule(
        id="ROS-SEC-007",
        category="security",
        severity=CRITICAL,
        title="Resolutor DNS abierto a Internet",
        recommendation=(
            "Descarte udp/53 y tcp/53 desde las interfaces WAN en `chain=input`, "
            "o desactive `allow-remote-requests` si el router no sirve DNS."
        ),
        check=_check_open_resolver,
    ),
    Rule(
        id="ROS-SEC-008",
        category="security",
        severity=HIGH,
        title="SNMP con comunidad `public`",
        recommendation=(
            "Renombre la comunidad, restrinja `addresses=` a los colectores y "
            "prefiera SNMPv3 donde sea posible."
        ),
        check=_check_snmp_public,
    ),
    Rule(
        id="ROS-SEC-009",
        category="security",
        severity=HIGH,
        title="Proxy HTTP o SOCKS habilitado",
        recommendation=(
            "Deshabilite `/ip proxy` y `/ip socks` salvo uso justificado: son "
            "vectores habituales de abuso de tránsito."
        ),
        check=_check_proxy_services,
    ),
    Rule(
        id="ROS-SEC-010",
        category="security",
        severity=MEDIUM,
        title="UPnP habilitado",
        recommendation=(
            "Deshabilite `/ip upnp`: permite que equipos internos abran puertos "
            "hacia Internet sin control."
        ),
        check=_check_upnp,
    ),
    Rule(
        id="ROS-SEC-011",
        category="security",
        severity=MEDIUM,
        title="Usuario `admin` activo",
        recommendation=(
            "Cree usuarios nominales y deshabilite o elimine `admin`: es el "
            "nombre que prueban primero los ataques de fuerza bruta."
        ),
        check=_check_admin_user,
    ),
    Rule(
        id="ROS-SEC-012",
        category="security",
        severity=MEDIUM,
        title="Usuario con grupo `full` sin restricción de origen",
        recommendation=(
            "Añada `address=` con los prefijos de gestión a cada usuario "
            "administrativo."
        ),
        check=_check_full_user_without_address,
    ),
    Rule(
        id="ROS-SEC-013",
        category="security",
        severity=MEDIUM,
        title="Servidor de bandwidth-test habilitado",
        recommendation=(
            "Deshabilite `/tool bandwidth-server`: permite consumir CPU y enlace "
            "desde la red."
        ),
        check=_check_bandwidth_server,
    ),
    Rule(
        id="ROS-SEC-014",
        category="security",
        severity=MEDIUM,
        title="MAC-Telnet o MAC-Winbox en todas las interfaces",
        recommendation=(
            "Limite `allowed-interface-list` a una lista de interfaces de "
            "gestión; en las interfaces de cliente evita el acceso por capa 2."
        ),
        check=_check_mac_server,
    ),
    Rule(
        id="ROS-SEC-015",
        category="security",
        severity=LOW,
        title="SSH sin `strong-crypto`",
        recommendation="Active `/ip ssh set strong-crypto=yes`.",
        check=_check_ssh_strong_crypto,
    ),
    Rule(
        id="ROS-SEC-016",
        category="security",
        severity=LOW,
        title="Descubrimiento de vecinos en todas las interfaces",
        recommendation=(
            "Restrinja `/ip neighbor discovery-settings "
            "discover-interface-list` a las interfaces de gestión."
        ),
        check=_check_neighbor_discovery,
    ),
    Rule(
        id="ROS-HYG-001",
        category="hygiene",
        severity=LOW,
        title="Identidad de fábrica (`MikroTik`)",
        recommendation=(
            "Asigne un nombre único con `/system identity set name=`: el "
            "inventario y los registros dependen de él."
        ),
        check=_check_default_identity,
    ),
    Rule(
        id="ROS-HYG-002",
        category="hygiene",
        severity=LOW,
        title="Sin cliente NTP",
        recommendation=(
            "Configure `/system ntp client` para que las marcas de tiempo de "
            "registros y respaldos sean comparables entre equipos."
        ),
        check=_check_ntp_client,
    ),
    Rule(
        id="ROS-HYG-003",
        category="hygiene",
        severity=HIGH,
        title="RouterOS por debajo de la versión mínima soportada",
        recommendation=(
            "Planifique la actualización; las versiones antiguas acumulan "
            "vulnerabilidades conocidas y no reciben parches."
        ),
        check=_check_routeros_version,
    ),
    Rule(
        id="ROS-BGP-001",
        category="bgp",
        severity=HIGH,
        title="Sesión BGP sin filtro de entrada",
        recommendation=(
            "Asocie una cadena de `/routing filter` a la entrada de cada sesión: "
            "sin filtro se acepta cualquier prefijo del vecino."
        ),
        check=_make_bgp_filter_check("input"),
    ),
    Rule(
        id="ROS-BGP-003",
        category="bgp",
        severity=LOW,
        title="Sesión BGP interna sin filtros",
        recommendation=(
            "Con infraestructura propia el riesgo es menor, pero conviene "
            "filtrar también en iBGP: evita que un error de redistribución en "
            "el otro extremo se propague por toda la red."
        ),
        check=_check_internal_bgp_without_filters,
    ),
    Rule(
        id="ROS-BGP-002",
        category="bgp",
        severity=HIGH,
        title="Sesión BGP sin filtro de salida",
        recommendation=(
            "Asocie una cadena de `/routing filter` a la salida de cada sesión "
            "para no reanunciar prefijos ajenos."
        ),
        check=_make_bgp_filter_check("output"),
    ),
)

RULES_BY_ID = {rule.id: rule for rule in RULES}


def rule_catalog() -> list[dict]:
    """Catálogo de reglas para el panel y la documentación."""
    return [
        {
            "id": rule.id,
            "category": rule.category,
            "severity": rule.severity,
            "title": rule.title,
            "recommendation": rule.recommendation,
        }
        for rule in RULES
    ]


def risk_score(counts: dict[str, int]) -> int:
    """Puntaje 0–100 a partir del recuento por severidad."""
    total = sum(
        SEVERITY_WEIGHT[severity] * counts.get(severity, 0)
        for severity in SEVERITY_ORDER
    )
    return min(total, 100)


def risk_level(counts: dict[str, int]) -> str:
    """Severidad del hallazgo más grave; `ok` si no hay ninguno."""
    for severity in SEVERITY_ORDER:
        if counts.get(severity):
            return severity
    return "ok"


def audit_config(config_text: str) -> dict:
    """Audita el texto de un respaldo y devuelve hallazgos con evidencia."""
    export = parse_export(config_text)
    findings = []
    counts = {severity: 0 for severity in SEVERITY_ORDER}
    for rule in RULES:
        evidence = rule.check(export)
        if not evidence:
            continue
        severidad = rule.severity_of(export) if rule.severity_of else rule.severity
        counts[severidad] += 1
        findings.append(
            {
                "rule_id": rule.id,
                "category": rule.category,
                "severity": severidad,
                "title": rule.title,
                "recommendation": rule.recommendation,
                # Los respaldos contienen las claves de los equipos a
                # propósito: nada que salga por la API va sin censurar.
                "evidence": [
                    {"detail": redact_secrets(item.detail), "line": item.line}
                    for item in evidence
                ],
            }
        )
    findings.sort(key=lambda f: (SEVERITY_ORDER.index(f["severity"]), f["rule_id"]))
    return {
        "ros_version": export.version,
        "rules_evaluated": len(RULES),
        "counts": counts,
        "score": risk_score(counts),
        "level": risk_level(counts),
        "findings": findings,
    }
