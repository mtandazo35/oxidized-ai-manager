#!/usr/bin/env bash
#
# Oxidized AI Manager — instalador rápido.
# Uso (dentro del repositorio clonado):
#     sudo ./install.sh
#
# Genera .env con secretos aleatorios, aplica el ajuste de kernel para Redis
# y levanta el stack. Idempotente: si .env ya existe, conserva sus valores.

set -euo pipefail

RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YLW=$'\033[1;33m'; NC=$'\033[0m'
info() { printf '%s[*]%s %s\n' "$GRN" "$NC" "$1"; }
warn() { printf '%s[!]%s %s\n' "$YLW" "$NC" "$1"; }
die()  { printf '%s[x]%s %s\n' "$RED" "$NC" "$1" >&2; exit 1; }

cd "$(dirname "$0")"

[ -f docker-compose.yml ] || die "Ejecute este script dentro del repositorio clonado."

# --- Dependencias ---
command -v docker >/dev/null 2>&1 || die "Docker no está instalado. Instálelo: https://docs.docker.com/engine/install/"
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
if [ -f .env ]; then
    warn ".env ya existe: conservo los valores actuales."
else
    info "Generando .env con secretos aleatorios."
    ADMIN_PASSWORD="$(openssl rand -hex 12)"
    cp .env.example .env
    set_secret() {
        local key="$1" val="$2"
        # Escapa & y \ para sed.
        val="$(printf '%s' "$val" | sed -e 's/[&\\]/\\&/g')"
        sed -i "s|^${key}=.*|${key}=${val}|" .env
    }
    set_secret POSTGRES_PASSWORD    "$(openssl rand -hex 32)"
    set_secret REDIS_PASSWORD       "$(openssl rand -hex 32)"
    set_secret APP_SECRET_KEY       "$(openssl rand -hex 32)"
    set_secret OXIDIZED_SOURCE_TOKEN "$(openssl rand -hex 32)"
    set_secret ADMIN_PASSWORD       "$ADMIN_PASSWORD"
    sed -i 's|^APP_ENV=.*|APP_ENV=production|' .env
    chmod 600 .env
fi

# --- Validar y levantar ---
info "Validando la configuración de Compose."
$COMPOSE config --quiet
info "Construyendo y levantando el stack (puede tardar en el primer build)."
$COMPOSE up -d --build

echo
info "Estado de los servicios:"
$COMPOSE ps

echo
info "Listo. Panel disponible en http://127.0.0.1:8000/"
info "Usuario: admin"
if [ -n "$ADMIN_PASSWORD" ]; then
    printf '%s[*]%s Clave inicial: %s%s%s\n' "$GRN" "$NC" "$YLW" "$ADMIN_PASSWORD" "$NC"
    info "Cámbiela desde el menú de usuario en su primer ingreso."
else
    info "Clave de admin: la definida en su .env (ADMIN_PASSWORD)."
fi
warn "Para exposición pública use deploy/docker-compose.public.yml + Nginx (vea docs/PUBLIC_ACCESS.md)."
