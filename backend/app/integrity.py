"""Revisión de integridad de un respaldo recién guardado.

Un respaldo que falla se ve: el nodo queda en rojo y alguien pregunta. Un
respaldo que se guarda **incompleto y en verde** no se ve, y es el que de verdad
duele, porque nadie descubre que no sirve hasta que hay que restaurar.

Eso pasó de verdad: el servidor SSH de RouterOS corta la salida al tamaño de
ventana de canal que anuncia el cliente y cierra el canal limpiamente, así que
Oxidized recibía medio `/export`, no veía ningún error y lo commiteaba como
respaldo bueno (ver oxidized/Dockerfile). Un core perdió más de la mitad de su
configuración durante meses sin una sola alerta.

Este módulo compara el export que acaba de entrar contra la versión anterior del
mismo equipo y devuelve avisos para el panel. No bloquea nada ni descarta el
respaldo: el respaldo cortado también es información, y descartarlo dejaría al
equipo sin nada. Lo que hace es dejar de mentir sobre su estado.

Todo aquí es una función pura sobre dos textos, para poder probarlo sin Oxidized,
sin red y sin base de datos.
"""

from __future__ import annotations

from dataclasses import dataclass

from .routeros_export import SENSITIVE_PATTERN, Export, parse_export


# `max_win_size` por defecto de net-ssh: 0x20000. Es el punto exacto donde
# MikroTik corta, así que un respaldo que pese casi eso es sospechoso por sí
# solo, incluso sin una versión anterior con la que compararlo.
VENTANA_SSH = 131072
BANDA_VENTANA = (130_500, 137_000)

# Por debajo de esta fracción del respaldo anterior se considera que encogió.
# 0.70 deja pasar la limpieza normal de un equipo (borrar clientes, colas o
# reglas) y atrapa un truncamiento, que se lleva bloques enteros.
UMBRAL_ENCOGIMIENTO = 0.70

# ...pero además tiene que ser una pérdida grande en términos absolutos. En un
# export de pocos cientos de bytes el porcentaje no dice nada: quitar tres
# líneas ya pasa del 30%. Con las dos condiciones juntas, el aviso solo salta
# cuando de verdad falta un pedazo de configuración.
MINIMO_PERDIDA = 2048

# Cuántas secciones desaparecidas se nombran antes de resumir.
MAX_SECCIONES_LISTADAS = 5


@dataclass(frozen=True)
class Integridad:
    """Medidas del respaldo y lo que hay que avisar sobre él."""

    tamano: int
    lineas: int
    secciones: int
    avisos: tuple[str, ...]

    @property
    def sospechoso(self) -> bool:
        return bool(self.avisos)


def _secciones(export: Export) -> list[str]:
    """Secciones presentes, en orden y sin repetir."""
    vistas: dict[str, None] = {}
    for stanza in export.stanzas:
        if stanza.section:
            vistas.setdefault(stanza.section, None)
    return list(vistas)


def _secretos_censurados(texto: str, export: Export) -> bool:
    """¿El export salió sin los secretos?

    Dos señales, porque RouterOS censura de dos maneras según el menú: unas
    veces deja el parámetro con el valor vacío y otras lo omite entero.
    """
    valores = [match.group(2) for match in SENSITIVE_PATTERN.finditer(texto)]
    if len(valores) >= 3 and all(valor in ('""', "", '"') for valor in valores):
        return True

    for seccion, clave in (
        ("/ppp secret", "password"),
        ("/interface wireguard", "private-key"),
    ):
        stanzas = [s for s in export.section(seccion) if s.command == "add"]
        if stanzas and not any(s.get(clave) for s in stanzas):
            return True
    return False


def revisar(
    nuevo: str, anterior: str | None = None, *, routeros: bool = True
) -> Integridad:
    """Mide el respaldo `nuevo` y lo compara con `anterior` si lo hay.

    `routeros=False` deja solo las comprobaciones que valen para cualquier
    equipo: las de secciones y secretos hablan la sintaxis de RouterOS y en una
    OLT darían avisos falsos.
    """
    avisos: list[str] = []
    tamano = len(nuevo.encode("utf-8", errors="replace"))
    lineas = len(nuevo.splitlines())
    # Un export de un core pesa cientos de kilobytes: se trocea una sola vez y
    # se reparte, en lugar de volver a parsearlo en cada comprobación.
    export = parse_export(nuevo) if routeros else Export()
    secciones_nuevas = _secciones(export)

    if not nuevo.strip():
        return Integridad(tamano, lineas, 0, ("El respaldo llegó vacío.",))

    if BANDA_VENTANA[0] <= tamano <= BANDA_VENTANA[1]:
        avisos.append(
            f"El respaldo pesa {tamano} bytes, justo en el límite de la ventana "
            f"SSH ({VENTANA_SSH}). Es la firma del truncamiento de RouterOS: "
            "conviene comprobar que el archivo termina donde debe."
        )

    if anterior:
        antes = len(anterior.encode("utf-8", errors="replace"))
        if (
            antes
            and tamano < antes * UMBRAL_ENCOGIMIENTO
            and antes - tamano >= MINIMO_PERDIDA
        ):
            caida = round((1 - tamano / antes) * 100)
            avisos.append(
                f"El respaldo encogió un {caida}%: de {antes} a {tamano} bytes. "
                "O el equipo cambió mucho, o llegó cortado."
            )
        if routeros:
            antes_secciones = _secciones(parse_export(anterior))
            faltan = [s for s in antes_secciones if s not in secciones_nuevas]
            if faltan:
                listadas = ", ".join(faltan[:MAX_SECCIONES_LISTADAS])
                resto = len(faltan) - MAX_SECCIONES_LISTADAS
                if resto > 0:
                    listadas += f" y {resto} más"
                avisos.append(f"Desaparecieron secciones que antes estaban: {listadas}.")

    if routeros:
        # Un export sin una sola sección no es una configuración. Lo típico es
        # que el equipo haya contestado con un error y ese texto se haya
        # guardado como si fuera el respaldo: pasó de verdad con un RouterOS 6
        # al que se le pidió `show-sensitive`, que no soporta. Se avisa sin
        # necesidad de una versión anterior con la que comparar.
        if not secciones_nuevas:
            avisos.append(
                "El respaldo no contiene ninguna sección de configuración. "
                "Suele ser la respuesta de error del equipo, guardada en lugar "
                "del export."
            )
        if not export.version:
            avisos.append(
                "El export no trae la cabecera con la versión de RouterOS: "
                "puede venir cortado desde el principio."
            )
        if _secretos_censurados(nuevo, export):
            avisos.append(
                "Los secretos salieron censurados, así que este respaldo no "
                "sirve para restaurar. Revise que la cuenta de respaldo tenga la "
                "policy `sensitive`."
            )

    return Integridad(tamano, lineas, len(secciones_nuevas), tuple(avisos))
