#!/usr/bin/env bash
#
# Oxidized AI Manager — instalador rápido y portable.
#
# Uso (dentro del repositorio clonado):
#     sudo ./install.sh                 # local (panel solo en 127.0.0.1:8000)
#     sudo ./install.sh --proxy RED     # detrás de un proxy externo (Nginx Proxy
#                                       # Manager): conecta el backend a esa red
#                                       # Docker y no publica el puerto en la LAN.
#
# El TLS, el certificado y las listas de acceso los gestiona el proxy externo,
# que no forma parte de este repositorio. Ver docs/PUBLIC_ACCESS.md.
#
# Idempotente: si .env ya existe, conserva sus valores.

set -euo pipefail

RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YLW=$'\033[1;33m'; NC=$'\033[0m'
info() { printf '%s[*]%s %s\n' "$GRN" "$NC" "$1"; }
warn() { printf '%s[!]%s %s\n' "$YLW" "$NC" "$1"; }
die()  { printf '%s[x]%s %s\n' "$RED" "$NC" "$1" >&2; exit 1; }

cd "$(dirname "$0")"

PROXY_NETWORK=""
while [ $# -gt 0 ]; do
    case "$1" in
        --proxy) PROXY_NETWORK="${2:-}"; shift 2 ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Opción desconocida: $1" ;;
    esac
done

[ -f docker-compose.yml ] || die "Ejecute este script dentro del repositorio clonado."

# --- Instalación de dependencias (VPS recién creado) ---
IS_ROOT=0; [ "$(id -u)" -eq 0 ] && IS_ROOT=1
SUDO=""; [ "$IS_ROOT" -eq 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"

apt_install() {
    if command -v apt-get >/dev/null 2>&1; then
        info "Instalando: $*"
        $SUDO apt-get update -qq
        DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq "$@" >/dev/null
    else
        die "Instale manualmente: $* (no se detectó apt)."
    fi
}

ensure_docker() {
    command -v docker >/dev/null 2>&1 && return
    warn "Docker no está instalado; instalándolo con el script oficial."
    [ "$IS_ROOT" -eq 1 ] || [ -n "$SUDO" ] || die "Se requiere root/sudo para instalar Docker."
    curl -fsSL https://get.docker.com | $SUDO sh
    $SUDO systemctl enable --now docker >/dev/null 2>&1 || true
}

command -v curl >/dev/null 2>&1 || apt_install curl ca-certificates
command -v openssl >/dev/null 2>&1 || apt_install openssl
ensure_docker

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    warn "Compose v2 ausente; instalando el plugin."
    apt_install docker-compose-plugin
    docker compose version >/dev/null 2>&1 || die "No se pudo habilitar Docker Compose v2."
    COMPOSE="docker compose"
fi

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

# --- Modo proxy externo: unir el backend a la red del proxy ---
if [ -n "$PROXY_NETWORK" ]; then
    docker network inspect "$PROXY_NETWORK" >/dev/null 2>&1 || \
        die "No existe la red Docker '$PROXY_NETWORK'. Véala con: docker network ls"

    # La subred de esa red es en quién confía uvicorn para leer X-Forwarded-For.
    PROXY_SUBNET="$(docker network inspect "$PROXY_NETWORK" \
        --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null | \
        tr -s ' ' | sed 's/ $//; s/ /,/g')"
    [ -n "$PROXY_SUBNET" ] || PROXY_SUBNET="127.0.0.1"

    set_kv PROXY_NETWORK       "$PROXY_NETWORK"
    set_kv FORWARDED_ALLOW_IPS "$PROXY_SUBNET"
    # El puerto deja de publicarse hacia la LAN: el proxy entra por la red Docker.
    set_kv API_BIND_ADDRESS    "127.0.0.1"

    info "Backend conectado a la red '$PROXY_NETWORK' (confía en $PROXY_SUBNET)."
    COMPOSE_FILES="-f docker-compose.yml -f deploy/docker-compose.proxy.yml"
else
    COMPOSE_FILES="-f docker-compose.yml"
    # Modo local: el panel queda solo en 127.0.0.1 del host. Para exponerlo use
    # un proxy externo con --proxy (ver docs/PUBLIC_ACCESS.md).
    set_kv API_BIND_ADDRESS    "127.0.0.1"
    set_kv FORWARDED_ALLOW_IPS "127.0.0.1"
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
API_PORT_VAL="$(grep '^API_PORT=' .env | cut -d= -f2)"; API_PORT_VAL="${API_PORT_VAL:-8000}"
if [ -n "$PROXY_NETWORK" ]; then
    info "Listo. Cree el proxy host en su Nginx Proxy Manager apuntando a:"
    printf '      %sbackend%s   puerto %s8000%s   (esquema http)\n' \
        "$YLW" "$NC" "$YLW" "$NC"
    info "Active ahí SSL + Force SSL + HTTP/2; WebSockets no hace falta."
    info "Suba client_max_body_size a 8m en Advanced (cargas .xlsx)."
    warn "HSTS: APP_ENABLE_HSTS=true en .env solo con el dominio ya confirmado."
else
    printf '%s[*]%s Listo. Panel en: %shttp://127.0.0.1:%s/%s\n' \
        "$GRN" "$NC" "$YLW" "$API_PORT_VAL" "$NC"
    info "Solo accesible desde el propio host. Para exponerlo: sudo ./install.sh --proxy <red>"
fi
info "Usuario: admin"
if [ -n "$ADMIN_PASSWORD" ]; then
    printf '%s[*]%s Clave inicial: %s%s%s\n' "$GRN" "$NC" "$YLW" "$ADMIN_PASSWORD" "$NC"
    info "Cámbiela desde el menú de usuario en su primer ingreso."
else
    info "Clave de admin: la definida en su .env (ADMIN_PASSWORD)."
fi
