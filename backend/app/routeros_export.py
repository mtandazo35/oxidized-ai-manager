"""Parser del texto de `/export` de RouterOS.

Convierte un respaldo de Oxidized en stanzas manejables por las reglas de
auditoría. Es puramente léxico: no conoce semántica de RouterOS, solo separa
secciones, comandos, parámetros y valores respetando comillas y expresiones
`[ find ... ]`.

Nota importante para las reglas: `/export` a secas **solo imprime los valores
distintos del predeterminado**. La ausencia de una sección no significa "no
configurado" sino "todo por defecto"; cada regla decide qué implica eso.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


VERSION_PATTERN = re.compile(r"by RouterOS (?P<version>[0-9][0-9a-zA-Z.\-]*)")


@dataclass(frozen=True)
class Stanza:
    """Una línea de comando dentro de una sección del export."""

    section: str
    command: str
    params: dict[str, str]
    positional: tuple[str, ...]
    line: int
    raw: str

    def get(self, key: str, default: str = "") -> str:
        return self.params.get(key, default)

    def is_true(self, key: str) -> bool:
        return self.params.get(key, "").lower() == "yes"

    def is_false(self, key: str) -> bool:
        return self.params.get(key, "").lower() == "no"


@dataclass
class Export:
    """Export completo ya troceado."""

    stanzas: list[Stanza] = field(default_factory=list)
    version: str = ""

    def section(self, path: str) -> list[Stanza]:
        """Stanzas de una sección exacta, p. ej. `/ip service`."""
        return [stanza for stanza in self.stanzas if stanza.section == path]

    def under(self, prefix: str) -> list[Stanza]:
        """Stanzas de una sección y de todas sus subsecciones."""
        return [
            stanza
            for stanza in self.stanzas
            if stanza.section == prefix or stanza.section.startswith(prefix + " ")
        ]

    def has_section(self, path: str) -> bool:
        return any(stanza.section == path for stanza in self.stanzas)


def _join_continuations(text: str) -> list[tuple[int, str]]:
    """Une las líneas partidas con `\\` y conserva el número de la primera."""
    joined: list[tuple[int, str]] = []
    buffer = ""
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not buffer:
            start = number
        if line.endswith("\\"):
            buffer += line[:-1].strip() + " "
            continue
        joined.append((start, (buffer + line.strip()).strip()))
        buffer = ""
    if buffer:
        joined.append((start, buffer.strip()))
    return joined


def _tokenize(body: str) -> list[str]:
    """Separa por espacios respetando comillas dobles y corchetes."""
    tokens: list[str] = []
    current = ""
    quoted = False
    depth = 0
    for char in body:
        if char == '"':
            quoted = not quoted
            current += char
        elif char == "[" and not quoted:
            depth += 1
            current += char
        elif char == "]" and not quoted:
            depth = max(depth - 1, 0)
            current += char
        elif char.isspace() and not quoted and depth == 0:
            if current:
                tokens.append(current)
                current = ""
        else:
            current += char
    if current:
        tokens.append(current)
    return tokens


def _unquote(value: str) -> str:
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def parse_export(text: str) -> Export:
    """Trocea un `/export` de RouterOS en secciones y comandos."""
    export = Export()
    section = ""
    for number, line in _join_continuations(text):
        if not line:
            continue
        if line.startswith("#"):
            match = VERSION_PATTERN.search(line)
            if match and not export.version:
                export.version = match.group("version")
            continue
        if line.startswith("/"):
            section = " ".join(line.split())
            continue
        tokens = _tokenize(line)
        if not tokens:
            continue
        params: dict[str, str] = {}
        positional: list[str] = []
        for token in tokens[1:]:
            if token.startswith("["):
                positional.append(token)
                continue
            key, separator, value = token.partition("=")
            if separator and key:
                params[key.lower()] = _unquote(value)
            else:
                positional.append(token)
        export.stanzas.append(
            Stanza(
                section=section,
                command=tokens[0].lower(),
                params=params,
                positional=tuple(positional),
                line=number,
                raw=line,
            )
        )
    return export


def version_tuple(version: str) -> tuple[int, ...]:
    """`7.23.2` -> `(7, 23, 2)`; ignora sufijos como `beta4` o `stable`."""
    parts: list[int] = []
    for chunk in version.split("."):
        digits = re.match(r"\d+", chunk)
        if not digits:
            break
        parts.append(int(digits.group()))
    return tuple(parts)


SENSITIVE_KEYS = (
    "password",
    "secret",
    "passphrase",
    "pre-shared-key",
    "psk",
    "private-key",
    "auth-password",
    "priv-password",
    "wpa-pre-shared-key",
    "wpa2-pre-shared-key",
)

SENSITIVE_PATTERN = re.compile(
    r"(?i)\b([\w.-]*(?:" + "|".join(re.escape(key) for key in SENSITIVE_KEYS) + r"))="
    r'("[^"]*"|\S*)'
)


def redact_secrets(text: str) -> str:
    """Enmascara valores sensibles de una línea de export.

    Los respaldos incluyen las claves a propósito (deben ser restaurables), así
    que cualquier evidencia que salga hacia la API o el panel pasa por aquí.
    """
    return SENSITIVE_PATTERN.sub(lambda m: f"{m.group(1)}=***", text)
