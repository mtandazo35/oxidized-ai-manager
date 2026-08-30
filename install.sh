#!/usr/bin/env bash
#
# Oxidized AI Manager — instalador rápido y portable.
#
# Uso (dentro del repositorio clonado):
#     sudo ./install.sh                 # local (panel en 127.0.0.1:8000)
#     sudo ./install.sh --public HOST   # publica con HTTPS tras Nginx (IP o dominio)
#     sudo ./install.sh --cert          # además emite/renueva el certificado (con --public)
#
# Idempotente: si .env ya existe, conserva sus valores.

set -euo pipefail

RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YLW=$'\033[1;33m'; NC=$'\033[0m'
info() { printf '%s[*]%s %s\n' "$GRN" "$NC" "$1"; }
warn() { printf '%s[!]%s %s\n' "$YLW" "$NC" "$1"; }
die()  { printf '%s[x]%s %s\n' "$RED" "$NC" "$1" >&2; exit 1; }

cd "$(dirname "$0")"

PUBLIC_HOST=""
ISSUE_CERT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --public) PUBLIC_HOST="${2:-}"; shift 2 ;;
        --cert)   ISSUE_CERT=1; shift ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Opción desconocida: $1" ;;
    esac
done

[ -f docker-compose.yml ] || die "Ejecute este script dentro del repositorio clonado."

# --- Dependencias ---
command -v docker >/dev/null 2>&1 || die "Docker no está instalado. https://docs.docker.com/engine/install/"
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    die "Docker Compose v2 no está disponible."
fi
command -v openssl >/dev/null 2>&1 || die "Falta 'openssl' para generar los secretos."

# --- Ajuste de kernel para la persistencia de Redis ---
if [ "$(id -u)" -eq 0 ]; then
    if ! sysctl vm.overcommit_memory 2>/dev/null | grep -q ' = 1'; then
        info "Aplicando vm.overcommit_memory = 1"
        echo 'vm.overcommit_memory = 1' > /etc/sysctl.d/99-oxidized-ai-manager.conf
        sysctl --load /etc/sysctl.d/99-oxidized-ai-manager.conf >/dev/null
    fi
else
    warn "Sin root: omito vm.overcommit_memory (Redis puede advertir en producción)."
fi

# --- .env con secretos aleatorios ---
ADMIN_PASSWORD=""
set_kv() {
    local key="$1" val="$2"
    val="$(printf '%s' "$val" | sed -e 's/[&\\|]/\\&/g')"
    if grep -q "^${key}=" .env; then
        sed -i "s|^${key}=.*|${key}=${val}|" .env
    else
        printf '%s=%s\n' "$key" "$val" >> .env
    fi
}
if [ -f .env ]; then
    warn ".env ya existe: conservo los valores actuales."
else
    info "Generando .env con secretos aleatorios."
    ADMIN_PASSWORD="$(openssl rand -hex 12)"
    cp .env.example .env
    set_kv POSTGRES_PASSWORD     "$(openssl rand -hex 32)"
    set_kv REDIS_PASSWORD        "$(openssl rand -hex 32)"
    set_kv APP_SECRET_KEY        "$(openssl rand -hex 32)"
    set_kv OXIDIZED_SOURCE_TOKEN "$(openssl rand -hex 32)"
    set_kv ADMIN_PASSWORD        "$ADMIN_PASSWORD"
    set_kv APP_ENV               "production"
    chmod 600 .env
fi

# --- Modo público: renderizar Nginx y (opcional) emitir certificado ---
if [ -n "$PUBLIC_HOST" ]; then
    set_kv PUBLIC_HOST "$PUBLIC_HOST"
    info "Renderizando deploy/nginx.conf para host: $PUBLIC_HOST"
    PUBLIC_HOST="$PUBLIC_HOST" envsubst '${PUBLIC_HOST}' \
        < deploy/nginx.conf.template > deploy/nginx.conf

    if [ "$ISSUE_CERT" -eq 1 ]; then
        command -v certbot >/dev/null 2>&1 || die "Certbot no está instalado (apt install certbot)."
        [ -f "/etc/letsencrypt/live/${PUBLIC_HOST}/fullchain.pem" ] || {
            info "Emitiendo certificado Let's Encrypt para $PUBLIC_HOST"
            certbot certonly --standalone -d "$PUBLIC_HOST" --non-interactive --agree-tos --register-unsafely-without-email
        }
    elif [ ! -f "/etc/letsencrypt/live/${PUBLIC_HOST}/fullchain.pem" ]; then
        warn "No hay certificado en /etc/letsencrypt/live/${PUBLIC_HOST}/."
        warn "Emítalo (certbot) o vuelva a correr con --cert antes de exponer HTTPS."
    fi
    COMPOSE_FILES="-f docker-compose.yml -f deploy/docker-compose.public.yml"
else
    COMPOSE_FILES="-f docker-compose.yml"
fi

# --- Validar y levantar ---
info "Validando la configuración de Compose."
$COMPOSE $COMPOSE_FILES config --quiet
info "Construyendo y levantando el stack (puede tardar en el primer build)."
$COMPOSE $COMPOSE_FILES up -d --build

echo
info "Estado de los servicios:"
$COMPOSE $COMPOSE_FILES ps

echo
if [ -n "$PUBLIC_HOST" ]; then
    info "Listo. Panel público en https://${PUBLIC_HOST}/"
else
    info "Listo. Panel local en http://127.0.0.1:8000/"
fi
info "Usuario: admin"
if [ -n "$ADMIN_PASSWORD" ]; then
    printf '%s[*]%s Clave inicial: %s%s%s\n' "$GRN" "$NC" "$YLW" "$ADMIN_PASSWORD" "$NC"
    info "Cámbiela desde el menú de usuario en su primer ingreso."
else
    info "Clave de admin: la definida en su .env (ADMIN_PASSWORD)."
fi
