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


# --- Regresiones de la QA -------------------------------------------------


def test_ningun_manejador_de_eventos_en_linea() -> None:
    """Los `onclick="fn('${esc(x)}')"` permitían XSS: `esc` no escapaba
    comillas y un nombre de usuario como `x');alert(1);('` se convertía en
    código. Además la CSP ya no admite manejadores en línea, así que uno
    nuevo dejaría el botón muerto sin ningún error visible."""
    html = PANEL.read_text(encoding="utf-8")
    en_linea = re.findall(r"""\son[a-z]+\s*=\s*["'`$]""", html)

    assert not en_linea, f"Manejadores en línea en el panel: {en_linea[:5]}"


def test_esc_escapa_comillas_y_angulares() -> None:
    js = panel_js()
    cuerpo = re.search(r"const ESC_MAPA = \{(.*?)\};", js, re.S)

    assert cuerpo, "esc() ya no usa ESC_MAPA: revise que siga escapando comillas"
    for caracter, entidad in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"),
                              ('"', "&quot;"), ("'", "&#39;")):
        assert entidad in cuerpo.group(1), f"esc() no escapa {caracter!r}"
    assert "innerText" not in re.search(r"function esc\(value\) \{(.*?)\n\}", js, re.S).group(1)


def test_esc_ejecutado_en_node() -> None:
    """Además de leer el código, se ejecuta: es la función que protege todo."""
    import json
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node no está instalado")
    js = panel_js()
    mapa = re.search(r"const ESC_MAPA = \{.*?\};", js, re.S).group(0)
    funcion = re.search(r"function esc\(value\) \{.*?\n\}", js, re.S).group(0)
    ataque = """x" onmouseover="alert(1)' <script>&"""
    programa = f"{mapa}\n{funcion}\nprocess.stdout.write(esc({json.dumps(ataque)}));"
    salida = subprocess.run([node, "-e", programa], capture_output=True, text=True, check=True).stdout

    assert salida == "x&quot; onmouseover=&quot;alert(1)&#39; &lt;script&gt;&amp;"


def test_toda_accion_de_los_botones_tiene_su_funcion() -> None:
    """Un `data-accion` sin entrada en ACCIONES es un botón que no hace nada."""
    js = panel_js()
    usadas = set(re.findall(r'data-accion="(\w+)"', PANEL.read_text(encoding="utf-8")))
    definidas = set(re.findall(r"^  (\w+): \(el\) =>", re.search(
        r"const ACCIONES = \{(.*?)\n\};", js, re.S).group(1), re.M))

    assert usadas, "no se encontró ningún data-accion"
    assert not usadas - definidas, f"acciones sin función: {sorted(usadas - definidas)}"


def test_sin_dialogos_nativos_del_navegador() -> None:
    """prompt(), alert() y confirm() no siguen el diseño ni el tema."""
    js = re.sub(r"//.*", "", panel_js())

    assert not re.search(r"(?<![\w.])(prompt|alert|confirm)\(", js)


def test_cerrar_sesion_detiene_los_sondeos_y_vacia_la_pantalla() -> None:
    """Cada ingreso sin recargar sumaba un setInterval y los datos de la
    cuenta anterior seguían en el DOM para el siguiente usuario."""
    js = panel_js()
    limpiar = re.search(r"function limpiarEstado\(\) \{(.*?)\n\}", js, re.S).group(1)
    entrar = re.search(r"async function enterApp\(\) \{(.*?)\n\}", js, re.S).group(1)

    assert "clearInterval(refreshTimer)" in limpiar
    assert "clearInterval(fastPollTimer)" in limpiar
    assert "allDevices = []" in limpiar and "devices-body" in limpiar
    assert "setInterval(refresh" not in entrar.replace("refreshTimer = setInterval(refresh", "")
    assert "clearInterval(refreshTimer)" in entrar
    assert re.search(r'fetch\("/api/auth/logout"', js), "cerrar sesión no revoca el token"


def test_el_login_distingue_bloqueo_de_clave_incorrecta() -> None:
    js = panel_js()
    login = re.search(r'\$\("login-form"\)\.addEventListener\("submit".*?\n\}\);', js, re.S).group(0)

    assert "429" in login and "403" in login


def test_la_carga_masiva_usa_una_sola_peticion() -> None:
    """Un POST por fila recargaba Oxidized una vez por equipo."""
    js = panel_js()
    subir = re.search(r'\$\("btn-upload"\)\.addEventListener\("click".*?\n\}\);', js, re.S).group(0)

    assert "/api/devices/import" in subir
    assert 'fetch("/api/devices"' not in subir
    assert "finally" in subir, "un error a mitad dejaba el botón deshabilitado"


def test_el_javascript_del_panel_compila(tmp_path) -> None:
    """Un error de sintaxis deja el panel en blanco (ni siquiera aparece el
    login) y ninguna prueba del backend lo nota. `node --check` lo detecta."""
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node no está instalado")
    html = PANEL.read_text(encoding="utf-8")
    for numero, codigo in enumerate(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)):
        archivo = tmp_path / f"script{numero}.js"
        archivo.write_text(codigo, encoding="utf-8")
        resultado = subprocess.run([node, "--check", str(archivo)], capture_output=True, text=True)
        assert resultado.returncode == 0, f"script {numero} no compila:\n{resultado.stderr}"


def test_cambiar_la_clave_guarda_el_token_nuevo() -> None:
    """Cambiar la clave invalida las sesiones abiertas; si el panel no se queda
    con el token que devuelve el servidor, la siguiente llamada da 401."""
    js = panel_js()
    form = re.search(r'\$\("pass-form"\)\.addEventListener\("submit".*?\n\}\);', js, re.S).group(0)

    assert "access_token" in form and 'localStorage.setItem("oxm_token"' in form


def test_las_tablas_grandes_se_paginan_y_ordenan() -> None:
    """Con cientos de equipos, pintar todas las filas en cada refresco hacía
    lento el panel. Cada tabla pinta solo su página."""
    html = PANEL.read_text(encoding="utf-8")
    js = panel_js()
    for funcion, tabla in (("renderDevices", "routers"), ("renderStatus", "estado")):
        cuerpo = re.search(rf"function {funcion}\(\) \{{(.*?)\n\}}", js, re.S).group(1)
        assert f'paginar("{tabla}", ordenar("{tabla}"' in cuerpo, f"{funcion} no pagina"
        assert f'id="pag-{tabla}"' in html, f"falta el paginador de {tabla}"
    # toda cabecera ordenable tiene su clave de orden
    claves = re.search(r"const CLAVES_ORDEN = \{(.*?)\n\};", js, re.S).group(1)
    for id_tabla in ("tabla-routers", "tabla-estado"):
        tabla = re.search(rf'<table id="{id_tabla}">.*?</table>', html, re.S).group(0)
        for orden in re.findall(r'data-orden="(\w+)"', tabla):
            assert f"{orden}:" in claves, f"{id_tabla}: no se puede ordenar por {orden}"


def test_las_preferencias_de_tabla_se_guardan() -> None:
    """Filas por página, orden, empresa y agrupado sobreviven a recargar."""
    js = panel_js()

    assert 'const PREFS_CLAVE = "oxm_tablas"' in js
    for preferencia in ("porPagina:", "agrupar:", "empresa:", "sentido:"):
        assert preferencia in js, f"no se guarda {preferencia}"
    assert "leerPrefs(tabla).empresa" in js, "la empresa guardada no se recupera"


def test_la_carga_masiva_vive_en_ajustes_y_no_vuelca_el_archivo() -> None:
    """La carga masiva pasó a Ajustes del sistema (también para operadores,
    que solo ven esa tarjeta) y el archivo elegido ya no se copia en el
    cuadro de texto: se anuncia como listo para cargar."""
    html = PANEL.read_text(encoding="utf-8")
    js = panel_js()

    assert 'id="tab-btn-import"' not in html and 'id="tab-import"' not in html
    assert 'data-seccion="carga"' in html and 'id="sec-carga"' in html
    assert re.search(r'if \(tab === "system"\) return perms\.can_write;', js)
    assert 'querySelectorAll(".hub-item[data-solo-admin]")' in js
    for seccion in ("version", "seguridad", "actividad", "config", "cuentas"):
        assert re.search(rf'data-seccion="{seccion}" data-solo-admin', html), seccion
    # Solo por archivo: no hay cuadro de texto donde volcarlo ni donde pegar.
    assert 'id="bulk-text"' not in html and "bulk-text" not in js
    lectura = re.search(r"async function leerArchivoCarga\(file\) \{(.*?)\n\}", js, re.S).group(1)
    assert "archivoListo(" in lectura
    assert re.search(r'\$\("bulk-zona"\)\.addEventListener\("drop"', js), "falta soltar el archivo"


def test_los_anchos_de_columna_se_pueden_ajustar_y_se_guardan() -> None:
    js = panel_js()

    assert "prepararAnchos(idTabla)" in js
    assert "guardarPrefs(TABLA_DE[idTabla], { anchos })" in js
    assert "suprimirClicOrden" in js, "soltar el asa no debe ordenar la columna"
    aplicar = re.search(r"function aplicarColumnas\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert 'aplicarAnchos("tabla-routers")' in aplicar


def test_respaldar_abre_la_ventana_en_vivo() -> None:
    """Respaldar ya no encadena avisos de texto: abre la ventana de respaldo,
    que valida, respalda y termina en el mismo sitio."""
    js = panel_js()
    uno = re.search(r"window\.backupNow = async \(id\) => \{(.*?)\n\};", js, re.S).group(1)
    tanda = re.search(r"async function runBulkBackup\(payload\) \{(.*?)\n\}", js, re.S).group(1)

    assert 'rsAbrir({ modo: "uno"' in uno and "avisar(" not in uno
    assert 'rsAbrir({\n      modo: "tanda"' in tanda and "Orden enviada" not in tanda
    assert 'rsTerminar(name, ok ? "ok" : "fallo")' in js
    assert 'rsTerminar(name, "sin-terminar")' in js
    for fase in ("validando", "curso", "ok", "fallo", "sin-terminar", "sin-acceso", "error"):
        assert re.search(rf'^\s+"?{re.escape(fase)}"?: \["', js, re.M), f"fase sin definir: {fase}"


def test_las_tablas_grandes_tienen_zona_propia_para_la_cabecera_fija() -> None:
    """La fila de títulos queda fija al desplazar. Con desplazamiento
    horizontal eso solo funciona si la tabla está dentro de su propia zona
    de desplazamiento: sin el contenedor, la cabecera se va con la página."""
    html = PANEL.read_text(encoding="utf-8")
    for id_tabla in ("tabla-routers", "tabla-estado"):
        assert re.search(rf'<div class="tabla-scroll">\s*<table id="{id_tabla}">', html), id_tabla
    css = html[html.index("/* tailwind:begin"):html.index("/* tailwind:end */")]
    assert re.search(r"thead th\{[^}]*position:sticky", css), "la cabecera ya no es fija"
