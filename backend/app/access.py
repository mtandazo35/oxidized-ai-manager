"""Control de acceso por IP: lista de permitidas y comprobaciones puras.

Aquí no hay base de datos ni red: solo el cálculo de si una IP entra o no.
Se separa a propósito porque es la pieza que, si se equivoca, deja a alguien
fuera de su propio panel.
"""

import ipaddress


# Redes que nunca se bloquean por fallos repetidos: son las de gestión y la
# del propio proxy. Bloquear la IP del proxy dejaría fuera a TODO el mundo,
# porque tras un proxy mal configurado todas las peticiones llegan con su IP.
NEVER_BLOCK = ("127.0.0.0/8", "::1/128")


def parse_networks(text: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Convierte una lista escrita por una persona en redes.

    Acepta separación por comas, espacios o saltos de línea, IPs sueltas
    (`10.0.0.5`, que equivale a `/32`) y redes (`10.0.0.0/24`). Lo que no se
    entiende se descarta en silencio: la validación con mensaje de error vive
    en la API, para que el operador se entere al guardar y no al intentar
    entrar.
    """
    networks = []
    for chunk in text.replace(",", " ").replace("\n", " ").split():
        try:
            networks.append(ipaddress.ip_network(chunk.strip(), strict=False))
        except ValueError:
            continue
    return networks


def invalid_entries(text: str) -> list[str]:
    """Trozos que no son una IP ni una red, para avisar al guardar."""
    malos = []
    for chunk in text.replace(",", " ").replace("\n", " ").split():
        try:
            ipaddress.ip_network(chunk.strip(), strict=False)
        except ValueError:
            malos.append(chunk.strip()[:40])
    return malos


def ip_in(text: str, ip: str) -> bool:
    """¿La IP está dentro de alguna de las redes de la lista?"""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in parse_networks(text))


def is_allowed(allowlist: str, enabled: bool, ip: str) -> bool:
    """¿Puede esta IP intentar iniciar sesión?

    Con la lista desactivada entra cualquiera. Con la lista activada pero
    **vacía** también: una lista vacía que negara todo dejaría el panel
    inaccesible en cuanto alguien activara la casilla sin rellenarla, y
    recuperarlo exigiría entrar por SSH a la base de datos.
    """
    if not enabled:
        return True
    if not parse_networks(allowlist):
        return True
    return ip_in(allowlist, ip) or ip_in(" ".join(NEVER_BLOCK), ip)


def is_never_blocked(ip: str, allowlist: str = "") -> bool:
    """IPs que nunca se bloquean automáticamente.

    Las de la lista de permitidas quedan exentas: si el operador ya dijo
    explícitamente que confía en esa red, un ataque de fuerza bruta desde
    fuera no debe poder dejarle fuera a él bloqueándola.
    """
    return ip_in(" ".join(NEVER_BLOCK), ip) or (
        bool(allowlist) and ip_in(allowlist, ip)
    )
