#!/usr/bin/env bash
#
# Respaldo del propio Oxidized AI Manager (no de los routers: eso lo hace
# Oxidized). Copia las tres piezas sin las cuales no se puede reconstruir:
#
#   1. PostgreSQL  — inventario, usuarios, eventos y ajustes (pg_dump).
#   2. oxidized_data — el repositorio Git con TODAS las configuraciones.
#   3. .env        — de él dependen APP_SECRET_KEY (las claves de los routers
#                    están cifradas con ella: sin .env el volcado es inútil).
#
# Uso:
#     ./scripts/backup-system.sh [destino]        # por defecto /root/backups
#     BACKUP_PASSPHRASE=... ./scripts/backup-system.sh   # cifra el paquete
#
# Con BACKUP_PASSPHRASE el paquete sale cifrado (AES-256, openssl enc) y es lo
# que debe salir del servidor. Sin ella el script avisa: un paquete en claro
# contiene las claves de todos los routers.
#
# Restauración: ver docs/BACKUP_RESTORE.md.

set -euo pipefail

RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YLW=$'\033[1;33m'; NC=$'\033[0m'
info() { printf '%s[*]%s %s\n' "$GRN" "$NC" "$1"; }
warn() { printf '%s[!]%s %s\n' "$YLW" "$NC" "$1"; }
die()  { printf '%s[x]%s %s\n' "$RED" "$NC" "$1" >&2; exit 1; }

cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"

DEST="${1:-/root/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

[ -f docker-compose.yml ] || die "Ejecute el script dentro del repositorio."
[ -f .env ] || die "No hay .env en $PROJECT_DIR."

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    die "No se encontró Docker Compose."
fi

mkdir -p "$DEST"
chmod 700 "$DEST"

# --- 1. PostgreSQL ---------------------------------------------------------
# shellcheck disable=SC1091
POSTGRES_USER="$(grep '^POSTGRES_USER=' .env | cut -d= -f2-)"
POSTGRES_DB="$(grep '^POSTGRES_DB=' .env | cut -d= -f2-)"
POSTGRES_USER="${POSTGRES_USER:-oxidized_ai}"
POSTGRES_DB="${POSTGRES_DB:-oxidized_ai}"

info "Volcando PostgreSQL ($POSTGRES_DB)."
$COMPOSE exec -T postgres pg_dump \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --clean --if-exists \
    > "$WORK/postgres.sql" || die "pg_dump falló: ¿está levantado el stack?"
[ -s "$WORK/postgres.sql" ] || die "El volcado de PostgreSQL salió vacío."

# --- 2. Volumen de Oxidized (backups.git) ----------------------------------
# Se lee desde un contenedor auxiliar para no depender de la ruta del volumen
# en el host ni de que Oxidized esté corriendo.
VOLUME="$($COMPOSE config --format json 2>/dev/null \
    | grep -o '"[a-z0-9_-]*_oxidized_data"' | head -1 | tr -d '"')"
VOLUME="${VOLUME:-oxidized-ai-manager_oxidized_data}"

info "Copiando el volumen de configuraciones ($VOLUME)."
docker run --rm -v "$VOLUME":/data:ro -v "$WORK":/out alpine:3.22 \
    tar czf /out/oxidized_data.tar.gz -C /data . \
    || die "No se pudo copiar el volumen $VOLUME."

# --- 3. .env ---------------------------------------------------------------
cp .env "$WORK/env.backup"

# --- Empaquetado -----------------------------------------------------------
cat > "$WORK/MANIFEST.txt" <<MANIFEST
Oxidized AI Manager — respaldo del sistema
fecha:      $(date -Is)
host:       $(hostname)
proyecto:   $PROJECT_DIR
contenido:  postgres.sql, oxidized_data.tar.gz, env.backup
ATENCIÓN:   oxidized_data.tar.gz contiene las configuraciones completas de los
            routers, claves incluidas. env.backup contiene APP_SECRET_KEY.
            Este paquete es tan sensible como el servidor entero.
MANIFEST

ARCHIVE="$DEST/oxidized-ai-manager-$STAMP.tar.gz"
tar czf "$ARCHIVE" -C "$WORK" postgres.sql oxidized_data.tar.gz env.backup MANIFEST.txt
chmod 600 "$ARCHIVE"

if [ -n "${BACKUP_PASSPHRASE:-}" ]; then
    info "Cifrando el paquete."
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
        -in "$ARCHIVE" -out "$ARCHIVE.enc" -pass env:BACKUP_PASSPHRASE
    chmod 600 "$ARCHIVE.enc"
    rm -f "$ARCHIVE"
    ARCHIVE="$ARCHIVE.enc"
else
    warn "Sin BACKUP_PASSPHRASE: el paquete queda SIN cifrar."
    warn "Contiene las claves de todos los routers; no lo saque así del host."
fi

# --- Retención -------------------------------------------------------------
find "$DEST" -maxdepth 1 -name 'oxidized-ai-manager-*.tar.gz*' \
    -mtime "+$RETENTION_DAYS" -delete 2>/dev/null || true

info "Listo: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
info "Copie el paquete FUERA del servidor y pruebe la restauración periódicamente."
