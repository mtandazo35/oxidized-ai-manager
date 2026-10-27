"""Orden del esquema SQL.

No hay PostgreSQL en las pruebas, así que se comprueba estáticamente lo que
rompió una instalación nueva: un `CREATE INDEX` sobre `devices (group_name)`
antes de crear la tabla y la columna. `init_schema` ejecuta `SCHEMA` de arriba
abajo, así que cada sentencia solo puede usar lo que ya se creó antes.
"""

import re

from app.db import SCHEMA


def _statements() -> list[str]:
    sin_comentarios = re.sub(r"--[^\n]*", "", SCHEMA)
    return [
        " ".join(sentencia.split())
        for sentencia in sin_comentarios.split(";")
        if sentencia.strip()
    ]


CREATE_TABLE = re.compile(
    r"^CREATE TABLE (?:IF NOT EXISTS )?(\w+) \((.*)\)$", re.IGNORECASE
)
ALTER_TABLE = re.compile(r"^ALTER TABLE (\w+) (.*)$", re.IGNORECASE)
ADD_COLUMN = re.compile(r"ADD COLUMN (?:IF NOT EXISTS )?(\w+)", re.IGNORECASE)
CREATE_INDEX = re.compile(
    r"^CREATE (?:UNIQUE )?INDEX (?:IF NOT EXISTS )?\w+ ON (\w+) \((.*)\)$",
    re.IGNORECASE,
)


def _columnas_de_create(cuerpo: str) -> set[str]:
    columnas = set()
    for definicion in cuerpo.split(","):
        palabra = definicion.strip().split(" ")[0].lower()
        if palabra and palabra not in ("primary", "unique", "constraint", "foreign"):
            columnas.add(palabra)
    return columnas


def _verificar(sentencias: list[str]) -> list[str]:
    """Devuelve los errores de orden encontrados (vacío = esquema válido)."""
    columnas: dict[str, set[str]] = {}
    errores = []
    for sentencia in sentencias:
        if match := CREATE_TABLE.match(sentencia):
            tabla = match.group(1).lower()
            columnas.setdefault(tabla, set()).update(
                _columnas_de_create(match.group(2))
            )
        elif match := ALTER_TABLE.match(sentencia):
            tabla = match.group(1).lower()
            if tabla not in columnas:
                errores.append(f"ALTER antes de CREATE TABLE {tabla}: {sentencia}")
                continue
            for columna in ADD_COLUMN.findall(match.group(2)):
                columnas[tabla].add(columna.lower())
        elif match := CREATE_INDEX.match(sentencia):
            tabla = match.group(1).lower()
            if tabla not in columnas:
                errores.append(f"índice antes de CREATE TABLE {tabla}: {sentencia}")
                continue
            for parte in match.group(2).split(","):
                columna = parte.strip().split(" ")[0].lower()
                if columna not in columnas[tabla]:
                    errores.append(
                        f"índice sobre {tabla}.{columna} antes de crear la "
                        f"columna: {sentencia}"
                    )
    return errores


def test_every_statement_is_understood() -> None:
    """Si alguien añade otro tipo de sentencia, esta prueba debe ampliarse."""
    for sentencia in _statements():
        assert (
            CREATE_TABLE.match(sentencia)
            or ALTER_TABLE.match(sentencia)
            or CREATE_INDEX.match(sentencia)
        ), sentencia


def test_schema_creates_tables_and_columns_before_using_them() -> None:
    assert _verificar(_statements()) == []


def test_the_check_detects_the_original_bug() -> None:
    """La comprobación tiene que fallar con el orden que rompió la instalación."""
    roto = [
        "CREATE INDEX IF NOT EXISTS devices_group_idx ON devices (group_name)",
        "CREATE TABLE IF NOT EXISTS devices ( id SERIAL PRIMARY KEY, name TEXT )",
        "ALTER TABLE devices ADD COLUMN IF NOT EXISTS group_name TEXT",
    ]
    assert _verificar(roto)

    sin_columna = [
        "CREATE TABLE IF NOT EXISTS devices ( id SERIAL PRIMARY KEY, name TEXT )",
        "CREATE INDEX IF NOT EXISTS devices_group_idx ON devices (group_name)",
        "ALTER TABLE devices ADD COLUMN IF NOT EXISTS group_name TEXT",
    ]
    assert _verificar(sin_columna)


def test_devices_keep_the_history_cutoff_column() -> None:
    """`history_since` existe y sin default: NULL = equipo anterior a la columna."""
    sentencia = next(
        s for s in _statements() if "history_since" in s and s.startswith("ALTER")
    )
    assert "DEFAULT" not in sentencia.upper()
    assert "NOT NULL" not in sentencia.upper()
