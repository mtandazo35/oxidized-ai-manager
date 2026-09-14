"""Comprobaciones estáticas del panel.

No hay entorno de pruebas de JavaScript en el proyecto, pero sí conviene
blindar los errores que ya han mordido una vez: son de los que no se ven en la
API ni en ningún test de backend, y en pantalla parecen otra cosa —el fallo
que motivó este archivo dejaba la tabla de routers vacía, como si se hubieran
borrado los equipos.
"""

import re
from pathlib import Path


PANEL = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def panel_js() -> str:
    html = PANEL.read_text(encoding="utf-8")
    return "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))


def test_no_multi_arg_function_is_passed_bare_to_filter_or_map() -> None:
    """`filter`, `map` y `forEach` llaman con (elemento, índice, array).

    Pasar por referencia una función con un segundo parámetro propio hace que
    ese parámetro reciba el índice. Fue exactamente lo que vació la tabla de
    routers: `deviceMatchesFilter(device, sufijo)` recibía `0` como sufijo y
    buscaba un campo inexistente.
    """
    js = panel_js()
    con_varios_parametros = {
        nombre
        for nombre, params in re.findall(r"function (\w+)\(([^)]*)\)", js)
        if len([p for p in params.split(",") if p.strip()]) >= 2
    }
    sospechosas = [
        nombre
        for nombre in re.findall(r"\.(?:filter|map|forEach)\((\w+)\)", js)
        if nombre in con_varios_parametros
    ]

    assert not sospechosas, (
        "Se pasan por referencia a filter/map/forEach funciones que esperan "
        f"más de un parámetro: {sospechosas}. Envuélvalas en una función "
        "flecha para no recibir el índice."
    )


def test_every_element_the_script_uses_exists_in_the_markup() -> None:
    """Un `id` renombrado en el HTML y no en el script deja media pantalla
    muerta sin que falle ninguna prueba del backend."""
    html = PANEL.read_text(encoding="utf-8")
    presentes = set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', html))
    usados = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', panel_js()))

    assert not usados - presentes, f"El script busca ids que no existen: {sorted(usados - presentes)}"


def test_no_section_hangs_outside_a_tab() -> None:
    """Una sección fuera de su `<div id="tab-...">` se ve en todas las
    pestañas a la vez. Ha pasado dos veces."""
    html = PANEL.read_text(encoding="utf-8")
    main = html[html.index("<main>"): html.index("</main>")]

    fuera, profundidad, dentro = [], 0, False
    for linea in main.splitlines():
        if re.search(r'<div id="tab-[a-z]+"', linea):
            dentro, profundidad = True, 0
        if dentro:
            profundidad += linea.count("<div") - linea.count("</div>")
            if profundidad <= 0 and "</div>" in linea:
                dentro = False
        elif linea.strip() and not linea.strip().startswith("<main"):
            fuera.append(linea.strip()[:60])

    assert not fuera, f"Contenido fuera de toda pestaña: {fuera}"


def test_tabs_declared_in_the_script_match_the_buttons() -> None:
    html = PANEL.read_text(encoding="utf-8")
    botones = set(re.findall(r'<button id="tab-btn-([a-z]+)"', html))
    declaradas = set(
        re.findall(r'"([a-z]+)"', re.search(r"const TAB_NAMES = \[([^\]]*)\]", panel_js()).group(1))
    )

    assert botones == declaradas, (
        f"La barra y TAB_NAMES no coinciden: solo botón {botones - declaradas}, "
        f"solo declaradas {declaradas - botones}"
    )
