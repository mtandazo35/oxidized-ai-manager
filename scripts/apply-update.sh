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

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/oxidized-ai-manager}"
CHANNEL_DIR="${CHANNEL_DIR:-$PROJECT_DIR/.update-channel}"
BRANCH="${UPDATE_BRANCH:-main}"
REQUEST="$CHANNEL_DIR/update-request.json"
STATUS="$CHANNEL_DIR/update-status.json"

now() { date -Is; }

# El estado es lo único que ve el panel: se escribe de forma atómica para que
# nunca lea un archivo a medio escribir.
set_state() {
    local state="$1" detail="$2" from="${3:-}" to="${4:-}"
    local tmp="$STATUS.tmp"
    printf '{"state":"%s","detail":%s,"at":"%s","from":"%s","to":"%s"}\n' \
        "$state" "$(printf '%s' "$detail" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" \
        "$(now)" "$from" "$to" > "$tmp"
    mv -f "$tmp" "$STATUS"
}

fail() {
    set_state "error" "$1" "${FROM:-}" ""
    echo "[x] $1" >&2
    exit 1
}

cd "$PROJECT_DIR" || { echo "No existe $PROJECT_DIR" >&2; exit 1; }
mkdir -p "$CHANNEL_DIR"

# Sin petición no hay nada que hacer (systemd puede despertar por otras causas).
[ -f "$REQUEST" ] || exit 0
REQUESTED_BY="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("requested_by","?"))' "$REQUEST" 2>/dev/null || echo '?')"
# Se borra ya: si algo falla más abajo, no queda un disparo repitiéndose.
rm -f "$REQUEST"

FROM="$(git rev-parse HEAD)"
set_state "en-progreso" "Actualización pedida por $REQUESTED_BY. Respaldando…" "$FROM" ""

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
else
    COMPOSE="docker-compose"
fi

# 1. Respaldo antes de tocar nada.
if [ -x "$PROJECT_DIR/scripts/backup-system.sh" ]; then
    "$PROJECT_DIR/scripts/backup-system.sh" /root/backups >/dev/null 2>&1 \
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

git merge --ff-only "origin/$BRANCH" --quiet \
    || fail "El repositorio local tiene cambios propios: resuélvalos a mano (git status)."

# 3. Reconstruir. Sin `down`: los contenedores se recrean en sitio y los
# volúmenes (PostgreSQL, respaldos de routers) ni se tocan.
set_state "en-progreso" "Reconstruyendo el stack…" "$FROM" "$TO"
$COMPOSE up -d --build </dev/null >/dev/null 2>&1 \
    || fail "Falló la reconstrucción. Revise: $COMPOSE logs --tail=100"

# La configuración de Oxidized vive en su volumen: si cambió en el repositorio,
# oxidized-init la re-siembra pero el proceso ya arrancado sigue con la vieja.
$COMPOSE restart oxidized </dev/null >/dev/null 2>&1 || true

# 4. Comprobar que quedó vivo.
for _ in $(seq 1 30); do
    if curl -fsS -m 3 http://127.0.0.1:8000/health/live >/dev/null 2>&1; then
        set_state "ok" "Actualizado de ${FROM:0:7} a ${TO:0:7} por $REQUESTED_BY." "$FROM" "$TO"
        exit 0
    fi
    sleep 2
done

fail "El backend no respondió tras actualizar. Respaldo previo en /root/backups/."
