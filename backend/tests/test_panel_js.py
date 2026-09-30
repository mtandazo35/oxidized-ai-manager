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


def test_el_panel_abre_siempre_en_routers() -> None:
    """Recargar devolvía al operador a la pestaña de la vez anterior.

    `switchTab` escribe la pestaña en el hash de la URL en cada clic, así que
    `initialTab` leyéndolo significaba abrir en Ajustes del sistema a quien
    hubiera terminado ahí. Se entra siempre por Routers.
    """
    js = panel_js()
    cuerpo = re.search(r"function initialTab\(\) \{(.*?)\n\}", js, re.S).group(1)

    assert 'return "dashboard";' in cuerpo
    assert "location.hash" not in cuerpo, (
        "initialTab vuelve a decidir por el hash: recargar dejaría al operador "
        "donde estaba, no en Routers"
    )


def test_cada_columna_ocultable_existe_en_la_cabecera_y_en_las_filas() -> None:
    """Un `data-col` en la lista que no esté en las celdas es una casilla que
    no oculta nada; uno en las celdas que no esté en la lista es una columna
    que no se puede volver a mostrar."""
    html = PANEL.read_text(encoding="utf-8")
    js = panel_js()
    declaradas = set(
        re.findall(r'\{ id: "(\w+)", etiqueta:', 
                   re.search(r"const COLUMNAS_ROUTERS = \[(.*?)\];", js, re.S).group(1))
    )
    tabla = re.search(r'<table id="tabla-routers">.*?</table>', html, re.S).group(0)
    cabecera = set(re.findall(r'<th data-col="(\w+)"', tabla))
    celdas = set(re.findall(r'<td data-col="(\w+)"', js))

    assert declaradas == cabecera == celdas, (
        f"declaradas {declaradas}, cabecera {cabecera}, celdas {celdas}"
    )


def test_el_colspan_de_la_tabla_de_routers_no_es_un_numero_fijo() -> None:
    """Con columnas ocultas, un colspan fijo saca la fila de grupo de la tabla."""
    js = panel_js()
    render = re.search(r"function renderDevices\(\) \{(.*?)\n\}", js, re.S).group(1)

    assert "columnasVisibles()" in render
    # ni en el mensaje de tabla vacía ni en NINGUNA de las llamadas que pintan
    # la fila de grupo: basta una con el número viejo para que esa agrupación
    # se salga de la tabla
    assert not re.search(r'colspan="\d+"', render)
    fijos = re.findall(r"agruparFilas\([^;]*?,\s*(\d+)\)", render)
    assert not fijos, f"agruparFilas con un ancho fijo: {fijos}"
    # y las filas se repintan al vuelo, así que hay que volver a ocultarlas
    assert "aplicarColumnas()" in render
