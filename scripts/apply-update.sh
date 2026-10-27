#!/usr/bin/env bash
#
# Aplica la actualización pedida desde el panel. Lo ejecuta systemd en el
# ANFITRIÓN (oxidized-ai-manager-updater.service), nunca el contenedor.
#
# El contenedor solo deja un archivo de petición; este script decide qué hacer
# y tiene **fijados** el remoto y la rama. La petición no aporta URL, rama ni
# commit: aunque alguien se hiciera con la cuenta de administrador del panel,
# lo único que consigue es desplegar el último commit legítimo de origin/main.
#
# Garantía de datos: respalda antes, y NUNCA usa `docker compose down`, ni
# mucho menos `-v`. Los volúmenes no se tocan; el esquema de PostgreSQL se
# migra solo al arrancar (todo es CREATE/ALTER ... IF NOT EXISTS).
#
# Frontera de confianza: este script corre como root, pero el directorio del
# canal pertenece al contenedor (uid 10001). Todo lo que hay dentro puede haber
# sido colocado por un backend comprometido: enlaces simbólicos, FIFOs,
# archivos enormes, directorios con el nombre esperado. Por eso aquí nunca se
# abre, se escribe ni se hace `chown` por nombre siguiendo enlaces: la lectura
# y la escritura del canal pasan por Python con O_NOFOLLOW y descriptores.

set -euo pipefail

# Por defecto, el repositorio en el que vive este mismo script; la unidad de
# systemd lo fija igualmente con Environment=PROJECT_DIR=...
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
CHANNEL_DIR="${CHANNEL_DIR:-$PROJECT_DIR/.update-channel}"
BRANCH="${UPDATE_BRANCH:-main}"
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
# Opcional: exigir que origin/$BRANCH esté firmado por una clave de confianza.
UPDATE_REQUIRE_SIGNED="${UPDATE_REQUIRE_SIGNED:-0}"
# Uid/gid con el que corre el backend dentro del contenedor.
CHANNEL_UID=10001
CHANNEL_GID=10001

# El estado es lo único que ve el panel. Se escribe así:
#   1. archivo temporal NUEVO (O_CREAT|O_EXCL|O_NOFOLLOW) dentro del canal,
#      abierto respecto a un descriptor del directorio (O_NOFOLLOW): un enlace
#      plantado con ese nombre hace fallar la apertura en vez de seguirse;
#   2. fchown/fchmod sobre el descriptor, no sobre el nombre: aunque el
#      contenedor renombre el temporal y ponga un enlace en su lugar, el chown
#      no alcanza ningún archivo del anfitrión;
#   3. rename() atómico sobre update-status.json. rename() reemplaza la ENTRADA
#      de directorio: si ahí hay un enlace simbólico o un FIFO, se sustituye el
#      enlace, nunca se escribe en su destino. Un directorio con ese nombre se
#      elimina si está vacío; si no, se aborta.
# El panel nunca lee un archivo a medio escribir. El dueño final es el uid del
# contenedor porque el backend reescribe el estado en sitio (write_text).
set_state() {
    local state="$1" detail="$2" from="${3:-}" to="${4:-}"
    python3 -I - "$CHANNEL_DIR" "$CHANNEL_UID" "$CHANNEL_GID" \
        "$state" "$detail" "$from" "$to" <<'PY'
import datetime, json, os, secrets, stat, sys

channel, uid, gid, state, detail, frm, to = sys.argv[1:8]
payload = json.dumps(
    {
        "state": state,
        "detail": detail,
        "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "from": frm,
        "to": to,
    },
    ensure_ascii=False,
).encode("utf-8") + b"\n"

dfd = os.open(channel, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
tmp = ".update-status.%s.tmp" % secrets.token_hex(8)
try:
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=dfd)
    try:
        view = memoryview(payload)
        while view:
            view = view[os.write(fd, view):]
        os.fchown(fd, int(uid), int(gid))
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    try:
        st = os.stat("update-status.json", dir_fd=dfd, follow_symlinks=False)
        if stat.S_ISDIR(st.st_mode):
            os.rmdir("update-status.json", dir_fd=dfd)
    except FileNotFoundError:
        pass
    os.rename(tmp, "update-status.json", src_dir_fd=dfd, dst_dir_fd=dfd)
except BaseException:
    try:
        os.unlink(tmp, dir_fd=dfd)
    except OSError:
        pass
    raise
finally:
    os.close(dfd)
PY
}

fail() {
    set_state "error" "$1" "${FROM:-}" "${TO:-}" || true
    echo "[x] $1" >&2
    exit 1
}

# Lee la petición sin fiarse de ella: solo un archivo regular (no enlace, no
# FIFO, no dispositivo), abierto con O_NOFOLLOW|O_NONBLOCK, y como mucho 4 KiB.
# Imprime el usuario que la pidió, saneado; sale con 2 si la petición no vale.
read_request() {
    python3 -I - "$CHANNEL_DIR" <<'PY'
import json, os, stat, sys

MAX = 4096
dfd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    try:
        fd = os.open("update-request.json",
                     os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
    except OSError:
        sys.exit(2)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            sys.exit(2)
        data = os.read(fd, MAX + 1)
    finally:
        os.close(fd)
finally:
    os.close(dfd)

if len(data) > MAX:
    sys.exit(2)
try:
    obj = json.loads(data.decode("utf-8"))
except ValueError:
    sys.exit(2)
who = obj.get("requested_by", "?") if isinstance(obj, dict) else "?"
who = "".join(c for c in str(who) if c.isprintable())[:64] or "?"
print(who)
PY
}

wait_healthy() {
    for _ in $(seq 1 30); do
        if curl -fsS -m 3 http://127.0.0.1:8000/health/live >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    return 1
}

# Lee una variable del .env sin ejecutarlo (sin `source`): última aparición,
# sin comillas envolventes.
env_get() {
    grep -E "^$1=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- \
        | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true
}

# Reconstruye y recrea en sitio. Sin `down`: los volúmenes ni se tocan.
# La configuración de Oxidized vive en su volumen: si cambió en el repositorio,
# oxidized-init la re-siembra pero el proceso ya arrancado sigue con la vieja.
# Se RECREA en lugar de reiniciarse: un `restart` conserva el /tmp del
# contenedor, y con él el PID viejo que hace que Oxidized se niegue a arrancar.
deploy_stack() {
    "${COMPOSE[@]}" up -d --build </dev/null >/dev/null 2>&1 || return 1
    "${COMPOSE[@]}" up -d --force-recreate oxidized </dev/null >/dev/null 2>&1 || true
}

# Tras el merge, cualquier fallo vuelve al commit anterior. `reset --keep` y no
# `--hard`: deja intactas las ediciones locales que no choquen (el .env y el
# canal ni siquiera están versionados) y se niega si chocaran, en vez de
# borrarlas en silencio. Las columnas que la versión nueva haya añadido a
# PostgreSQL se quedan: la anterior las ignora.
rollback() {
    local motivo="$1"
    set_state "en-progreso" "$motivo Volviendo a ${FROM:0:7}…" "$FROM" "$TO" || true
    if git reset --quiet --keep "$FROM" && deploy_stack && wait_healthy; then
        fail "$motivo Se revirtió a ${FROM:0:7}: el panel sigue en la versión anterior. Respaldo previo en $BACKUP_DIR/."
    fi
    fail "$motivo La vuelta atrás a ${FROM:0:7} TAMBIÉN falló: hace falta intervención manual (git log, ${COMPOSE[*]} logs --tail=100). Respaldo previo en $BACKUP_DIR/."
}

cd "$PROJECT_DIR" || { echo "No existe $PROJECT_DIR" >&2; exit 1; }
if [ ! -d "$CHANNEL_DIR" ] || [ -L "$CHANNEL_DIR" ]; then
    echo "El canal $CHANNEL_DIR no existe o es un enlace." >&2
    exit 1
fi

REQUEST="$CHANNEL_DIR/update-request.json"

# Sin petición no hay nada que hacer (systemd puede despertar por otras causas).
# `-L` además de `-e`: un enlace roto también es "algo" que hay que consumir.
[ -e "$REQUEST" ] || [ -L "$REQUEST" ] || exit 0

if [ -f "$REQUEST" ] && [ ! -L "$REQUEST" ] \
        && REQUESTED_BY="$(read_request)"; then
    :
else
    # rm -f sobre un enlace borra el enlace, no su destino.
    rm -f "$REQUEST"
    fail "Petición de actualización inválida (no es un archivo regular, es un enlace o supera 4 KiB); se ignoró."
fi
# Se borra ya: si algo falla más abajo, no queda un disparo repitiéndose.
rm -f "$REQUEST"

FROM="$(git rev-parse HEAD)"
TO=""
set_state "en-progreso" "Actualización pedida por $REQUESTED_BY. Respaldando…" "$FROM" ""

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
else
    COMPOSE=(docker-compose)
fi

# Mismos archivos de Compose que usó install.sh. Con proxy externo, install.sh
# deja COMPOSE_FILE en el .env y Compose lo lee solo (igual que en los
# comandos a mano). Las instalaciones anteriores tienen PROXY_NETWORK pero no
# COMPOSE_FILE: se les añade aquí una vez, y si no se pudiera, se pasa el
# overlay con -f. Sin esto el backend se recrearía fuera de la red de NPM.
if [ -z "$(env_get COMPOSE_FILE)" ] && [ -n "$(env_get PROXY_NETWORK)" ]; then
    if printf '\n# Añadido por apply-update.sh: overlay del proxy externo (ver docs/PUBLIC_ACCESS.md).\nCOMPOSE_FILE=docker-compose.yml:deploy/docker-compose.proxy.yml\n' >> .env; then
        echo "[*] .env: añadido COMPOSE_FILE con el overlay del proxy." >&2
    else
        COMPOSE+=(-f docker-compose.yml -f deploy/docker-compose.proxy.yml)
    fi
fi

# 1. Respaldo antes de tocar nada.
if [ -x "$PROJECT_DIR/scripts/backup-system.sh" ]; then
    "$PROJECT_DIR/scripts/backup-system.sh" "$BACKUP_DIR" >/dev/null 2>&1 \
        || fail "El respaldo previo falló; no se actualiza nada."
else
    fail "Falta scripts/backup-system.sh: no se actualiza sin respaldo previo."
fi

# 2. Traer los cambios. --ff-only: si alguien editó archivos en el servidor,
# el pull falla en lugar de intentar mezclar y dejar el árbol a medias.
set_state "en-progreso" "Descargando cambios de origin/$BRANCH…" "$FROM" ""
git fetch --quiet origin "$BRANCH" || fail "No se pudo contactar con el remoto."
TO="$(git rev-parse "origin/$BRANCH")"

if [ "$FROM" = "$TO" ]; then
    set_state "al-dia" "Ya estaba en la última versión ($(git rev-parse --short HEAD))." "$FROM" "$TO"
    exit 0
fi

# Opcional (UPDATE_REQUIRE_SIGNED=1 en la unidad): quien controle la cuenta de
# GitHub no basta para desplegar; el commit tiene que estar firmado con una
# clave en el llavero de root (o en gpg.ssh.allowedSignersFile). Ver
# docs/UPDATES.md.
if [ "$UPDATE_REQUIRE_SIGNED" = "1" ]; then
    git verify-commit "$TO" >/dev/null 2>&1 \
        || fail "origin/$BRANCH (${TO:0:7}) no tiene una firma válida de confianza; no se actualiza."
fi

git merge --ff-only "origin/$BRANCH" --quiet \
    || fail "El repositorio local tiene cambios propios: resuélvalos a mano (git status)."

# 3. Reconstruir (y volver atrás si falla).
set_state "en-progreso" "Reconstruyendo el stack…" "$FROM" "$TO"
deploy_stack || rollback "Falló la reconstrucción de ${TO:0:7}."

# 4. Comprobar que quedó vivo.
wait_healthy || rollback "El backend no respondió tras actualizar a ${TO:0:7}."

set_state "ok" "Actualizado de ${FROM:0:7} a ${TO:0:7} por $REQUESTED_BY." "$FROM" "$TO"
