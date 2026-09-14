"""Extrae credenciales PPPoE de un respaldo ya guardado.

Sirve para lo que un ISP necesita a diario: recuperar el usuario y la clave de
un cliente sin entrar al router. Trabaja **solo** sobre el texto del respaldo,
que ya está en `backups.git`; no abre sesión contra ningún equipo.

Para que estos datos existan en el respaldo hacen falta dos cosas:

- que Oxidized no censure los secretos (`remove_secret` desactivado, que es
  como está), y
- que la cuenta de respaldo del MikroTik tenga la policy `sensitive`.

Sin la segunda, RouterOS devuelve el export sin contraseñas y aquí saldrán
vacías. Esa diferencia se informa en el resultado para que no parezca un fallo
de la plataforma.
"""

from .routeros_export import parse_export


def _clean(value: str) -> str:
    return value.strip()


def extract_pppoe(config_text: str) -> dict:
    """Usuarios PPPoE del respaldo: los del servidor y los de cliente.

    `/ppp secret` son las cuentas que el router entrega a sus clientes (el caso
    habitual en un ISP). `/interface pppoe-client` es el propio router
    conectándose contra otro, y también lleva usuario y clave.
    """
    export = parse_export(config_text)
    secretos = []
    for stanza in export.section("/ppp secret"):
        if stanza.command != "add":
            continue
        nombre = _clean(stanza.get("name"))
        if not nombre:
            continue
        secretos.append(
            {
                "user": nombre,
                "password": _clean(stanza.get("password")),
                "profile": _clean(stanza.get("profile")),
                "service": _clean(stanza.get("service")),
                "remote_address": _clean(stanza.get("remote-address")),
                "comment": _clean(stanza.get("comment")),
                "enabled": not stanza.is_true("disabled"),
                "line": stanza.line,
            }
        )

    clientes = []
    for stanza in export.section("/interface pppoe-client"):
        if stanza.command != "add":
            continue
        clientes.append(
            {
                "interface": _clean(stanza.get("name")),
                "user": _clean(stanza.get("user")),
                "password": _clean(stanza.get("password")),
                "comment": _clean(stanza.get("comment")),
                "enabled": not stanza.is_true("disabled"),
                "line": stanza.line,
            }
        )

    secretos.sort(key=lambda item: item["user"].lower())
    con_clave = sum(1 for item in secretos if item["password"])
    return {
        "secrets": secretos,
        "clients": clientes,
        "total": len(secretos),
        "with_password": con_clave,
        # Si hay cuentas pero ninguna trae clave, el export vino censurado:
        # casi siempre es que falta la policy `sensitive` en el MikroTik.
        "censored": bool(secretos) and con_clave == 0,
    }
