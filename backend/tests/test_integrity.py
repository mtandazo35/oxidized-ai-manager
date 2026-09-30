from app.integrity import BANDA_VENTANA, revisar


CABECERA = (
    "# 2026-09-29 08:00:00 by RouterOS 7.23.2\n"
    "# software id = ABCD-1234\n"
)

COMPLETO = (
    CABECERA
    + "/interface bridge\n"
    + "add name=bridge-lan\n"
    + "/ip address\n"
    + "add address=192.0.2.1/24 interface=bridge-lan\n"
    + "/ppp secret\n"
    + 'add name=cliente1 password="Cl4v3Uno" service=pppoe\n'
    + 'add name=cliente2 password="Cl4v3Dos" service=pppoe\n'
    + "/ip firewall filter\n"
    + "add action=drop chain=input src-address=198.51.100.0/24\n"
)

RELLENO = "add action=accept chain=forward comment=regla-de-relleno\n"

# Un export real pesa decenas de kilobytes, y las pruebas de proporción
# necesitan ese orden de magnitud para decir algo.
GRANDE = COMPLETO + RELLENO * 500


def test_respaldo_completo_no_genera_avisos() -> None:
    veredicto = revisar(COMPLETO)

    assert veredicto.avisos == ()
    assert not veredicto.sospechoso
    assert veredicto.secciones == 4
    assert veredicto.lineas == len(COMPLETO.splitlines())


def test_respaldo_vacio_se_avisa_y_no_se_mide_nada_mas() -> None:
    veredicto = revisar("   \n")

    assert veredicto.sospechoso
    assert veredicto.avisos == ("El respaldo llegó vacío.",)


def test_tamano_en_el_limite_de_la_ventana_ssh_es_sospechoso() -> None:
    """La firma del bug de RouterOS: cortado justo en los 128 KiB del canal."""
    texto = CABECERA + "/ip firewall filter\n"
    texto += RELLENO * ((BANDA_VENTANA[0] - len(texto)) // len(RELLENO) + 1)
    assert BANDA_VENTANA[0] <= len(texto.encode()) <= BANDA_VENTANA[1]

    veredicto = revisar(texto)

    assert any("ventana SSH" in aviso for aviso in veredicto.avisos)


def test_respaldo_que_encoge_mucho_se_avisa_con_el_porcentaje() -> None:
    veredicto = revisar(COMPLETO, GRANDE)

    assert any("encogió un" in aviso for aviso in veredicto.avisos)


def test_cambio_normal_de_configuracion_no_dispara_el_encogimiento() -> None:
    """Borrar unas reglas es normal; el umbral no debe convertirlo en alarma."""
    veredicto = revisar(GRANDE, GRANDE + RELLENO * 5)

    assert not any("encogió" in aviso for aviso in veredicto.avisos)


def test_export_corto_que_pierde_pocos_bytes_no_alarma() -> None:
    """Sin el mínimo absoluto, quitar cinco líneas de un export corto saltaba."""
    veredicto = revisar(COMPLETO, COMPLETO + RELLENO * 5)

    assert not any("encogió" in aviso for aviso in veredicto.avisos)


def test_secciones_que_desaparecen_se_nombran() -> None:
    cortado = CABECERA + "/interface bridge\nadd name=bridge-lan\n"

    veredicto = revisar(cortado, COMPLETO)

    faltan = [a for a in veredicto.avisos if "Desaparecieron secciones" in a]
    assert faltan
    assert "/ppp secret" in faltan[0]
    assert "/ip firewall filter" in faltan[0]


def test_secretos_omitidos_por_falta_de_show_sensitive() -> None:
    """RouterOS 6.43+ omite el parámetro entero cuando censura."""
    censurado = (
        CABECERA
        + "/ppp secret\n"
        + "add name=cliente1 service=pppoe\n"
        + "add name=cliente2 service=pppoe\n"
    )

    veredicto = revisar(censurado)

    assert any("censurados" in aviso for aviso in veredicto.avisos)


def test_secretos_vaciados_tambien_se_detectan() -> None:
    censurado = (
        CABECERA
        + "/interface wireguard\n"
        + 'add name=wg0 private-key=""\n'
        + "/snmp community\n"
        + 'set [ find default=yes ] authentication-password="" '
        'encryption-password=""\n'
    )

    veredicto = revisar(censurado)

    assert any("censurados" in aviso for aviso in veredicto.avisos)


def test_secretos_presentes_no_se_reportan_como_censurados() -> None:
    veredicto = revisar(COMPLETO)

    assert not any("censurados" in aviso for aviso in veredicto.avisos)


def test_export_sin_cabecera_de_version_es_sospechoso() -> None:
    sin_cabecera = "/ip address\nadd address=192.0.2.1/24\n"

    veredicto = revisar(sin_cabecera)

    assert any("cabecera" in aviso for aviso in veredicto.avisos)


def test_equipo_que_no_es_routeros_solo_recibe_las_pruebas_genericas() -> None:
    """En una OLT las secciones con `/` y los secretos de RouterOS no aplican."""
    volcado = "config vlan 10\n exit\n"

    veredicto = revisar(volcado, routeros=False)

    assert veredicto.avisos == ()
