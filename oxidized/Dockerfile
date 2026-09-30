# Oxidized con el parche del truncamiento de RouterOS.
#
# EL PROBLEMA
# MikroTik recorta la salida SSH al tamaño de ventana de canal que anuncia el
# cliente. net-ssh anuncia 128 KiB (`max_win_size` por defecto, 0x20000), así
# que cualquier `/export` más grande que eso llega cortado. Y llega cortado en
# silencio: el router cierra el canal limpiamente, `exec!` devuelve lo que
# alcanzó a leer sin lanzar ninguna excepción y Oxidized commitea ese archivo
# incompleto como si fuera un respaldo correcto.
#
# Hacen falta dos condiciones a la vez, y por eso no afecta a toda la flota:
#   1. que el export supere los ~128 KiB, y
#   2. que haya latencia suficiente para que la ventana TCP crezca por encima
#      de la ventana del canal.
# En LAN no se dispara nunca aunque el config sea enorme. Contra un router
# remoto con un config grande, sí. OpenSSH anuncia 2 MB, y de ahí que un
# `ssh router "/export"` a mano funcione y Oxidized falle contra el mismo
# equipo.
#
# EL ARREGLO Y POR QUÉ ESTÁ AQUÍ Y NO EN LA IMAGEN OFICIAL
# Upstream ya lo corrigió subiendo la ventana a 2 MB (issue #3867), pero el
# cambio está en `[Unreleased]` del CHANGELOG: la última versión publicada es
# la 0.37.0 del 2026-05-20, que es la que usamos, y no lo trae. Tampoco se
# puede resolver por configuración, porque Oxidized fija `max_win_size` en
# código sin exponerlo como variable.
#
# CUÁNDO BORRAR ESTE ARCHIVO
# Cuando se publique una versión de Oxidized que incluya el arreglo: subir
# `OXIDIZED_BASE_IMAGE`, y este Dockerfile detectará que el gem ya trae
# `max_win_size` y no tocará nada. Comprobado, el parche se vuelve inerte solo.
# A partir de ahí se puede eliminar, junto con el bloque `build:` del
# docker-compose.

ARG OXIDIZED_BASE_IMAGE=oxidized/oxidized:0.37.0
FROM ${OXIDIZED_BASE_IMAGE}

# El modelo propio no vive en la imagen sino en el volumen (lo siembra
# `oxidized-init`, que es donde Oxidized lo busca). Se copia aquí solo para
# comprobar su sintaxis: un error de Ruby en el modelo dejaría a la flota
# entera sin respaldo en tiempo de ejecución, y así el build falla antes.
COPY model/routeros.rb /tmp/model-check/routeros.rb
RUN ruby -c /tmp/model-check/routeros.rb && rm -rf /tmp/model-check

# La imagen oficial no declara USER: corre como root y runit baja privilegios a
# la cuenta `oxidized` (uid 30000). No se toca eso aquí; poner un USER al final
# rompería runsvdir.
RUN set -eu; \
    archivo="$(gem contents oxidized 2>/dev/null | grep -m1 'input/sshbase\.rb$' || true)"; \
    if [ -z "$archivo" ]; then \
        archivo="$(find / -xdev -type f -path '*/oxidized/input/sshbase.rb' 2>/dev/null | head -n1)"; \
    fi; \
    if [ -z "$archivo" ]; then \
        echo "ERROR: no se encontró sshbase.rb en la imagen base." >&2; \
        exit 1; \
    fi; \
    echo "sshbase.rb: $archivo"; \
    if grep -q 'max_win_size' "$archivo"; then \
        echo "Ya trae max_win_size: la imagen base incluye el arreglo de #3867."; \
        echo "Se puede eliminar oxidized/Dockerfile y el build: del compose."; \
    else \
        sed -i 's|^\([[:space:]]*\)forward_agent:\([[:space:]]*\)false$|\1forward_agent:\2false,\n\1max_win_size:                    2 * 1024 * 1024 # Issue #3867 - Mikrotik ROSSH|' "$archivo"; \
        if ! grep -q 'max_win_size' "$archivo"; then \
            echo "ERROR: el parche no se aplicó. Cambió sshbase.rb en la imagen base." >&2; \
            exit 1; \
        fi; \
        ruby -c "$archivo"; \
        echo "Parche aplicado: ventana de canal SSH a 2 MB."; \
    fi
