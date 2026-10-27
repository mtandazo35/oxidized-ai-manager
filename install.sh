#!/usr/bin/env bash
#
# Oxidized AI Manager — instalador rápido y portable.
#
# Uso (dentro del repositorio clonado):
#     sudo ./install.sh                 # local (panel solo en 127.0.0.1:8000)
#     sudo ./install.sh --proxy RED     # detrás de un proxy externo (Nginx Proxy
#                                       # Manager): conecta el backend a esa red
#                                       # Docker y no publica el puerto en la LAN.
#     sudo ./install.sh --systemd       # además instala las unidades de systemd
#                                       # (botón "Actualizar" y respaldo diario)
#                                       # con la ruta real del repositorio.
#
# Ruta recomendada del repositorio: /opt/oxidized-ai-manager (la que traen las
# unidades de deploy/). Con --systemd funciona desde cualquier otra.
#
# Con --proxy se escribe COMPOSE_FILE en .env, así que `docker compose ...` a
# mano, el botón "Actualizar" y el respaldo usan siempre el overlay del proxy.
# Si .env ya tiene PROXY_NETWORK, volver a ejecutar el script sin --proxy
# conserva el modo proxy.
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
INSTALL_SYSTEMD=0
while [ $# -gt 0 ]; do
    case "$1" in
        --proxy)
            [ -n "${2:-}" ] || die "--proxy necesita el nombre de la red Docker."
            PROXY_NETWORK="$2"; shift 2 ;;
        --systemd) INSTALL_SYSTEMD=1; shift ;;
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

# --- Canal de actualización (botón "Actualizar" del panel) ---
# El backend corre como uid 10001 dentro del contenedor: el directorio debe
# pertenecerle o no podrá dejar la petición.
CHANNEL_DIR="$(pwd)/.update-channel"
mkdir -p "$CHANNEL_DIR"
chown -R 10001:10001 "$CHANNEL_DIR" 2>/dev/null || \
    warn "No se pudo ajustar el dueño de $CHANNEL_DIR (¿sin root?): el botón de actualizar fallará."
chmod 700 "$CHANNEL_DIR"
set_kv UPDATE_CHANNEL_DIR "./.update-channel"

# --- Modo proxy externo: unir el backend a la red del proxy ---
# Una instalación con proxy no debe perderlo por volver a ejecutar el script
# sin --proxy: se reutiliza la red que ya figura en .env.
if [ -z "$PROXY_NETWORK" ]; then
    PROXY_NETWORK="$(grep -E '^PROXY_NETWORK=' .env | tail -n 1 | cut -d= -f2- || true)"
    [ -z "$PROXY_NETWORK" ] || \
        info "Modo proxy conservado: .env ya define PROXY_NETWORK=$PROXY_NETWORK."
fi
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

    # Compose lee COMPOSE_FILE del .env: así los comandos a mano
    # (`docker compose up -d`, `ps`, `logs`), el botón "Actualizar" y el
    # respaldo usan los mismos archivos que esta instalación, sin -f.
    set_kv COMPOSE_FILE        "docker-compose.yml:deploy/docker-compose.proxy.yml"

    info "Backend conectado a la red '$PROXY_NETWORK' (confía en $PROXY_SUBNET)."
    COMPOSE_FILES="-f docker-compose.yml -f deploy/docker-compose.proxy.yml"
else
    COMPOSE_FILES="-f docker-compose.yml"
    # Sin red de proxy, un COMPOSE_FILE con el overlay haría fallar a Compose.
    sed -i '/^COMPOSE_FILE=.*docker-compose\.proxy\.yml/d' .env
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
# --- Unidades de systemd (solo con --systemd) ---
# Las unidades de deploy/ traen la ruta canónica /opt/oxidized-ai-manager; aquí
# se sustituye por la ruta real del repositorio. Idempotente: reescribe las
# cuatro unidades y vuelve a habilitar, sin duplicar nada.
UNIT_DIR=/etc/systemd/system
CANONICAL_DIR=/opt/oxidized-ai-manager
if [ "$INSTALL_SYSTEMD" -eq 1 ]; then
    [ "$IS_ROOT" -eq 1 ] || die "--systemd requiere root (sudo ./install.sh --systemd)."
    command -v systemctl >/dev/null 2>&1 || die "--systemd: no se encontró systemctl."
    REPO_DIR="$(pwd -P)"
    # systemd no admite espacios sin escapar en estas rutas y sed usa '|'.
    case "$REPO_DIR" in
        *[[:space:]]*|*'|'*|*'&'*|*'\'*) die "--systemd: la ruta '$REPO_DIR' tiene caracteres no admitidos." ;;
    esac
    for unit in oxidized-ai-manager-updater.path oxidized-ai-manager-updater.service \
                oxidized-ai-manager-backup.service oxidized-ai-manager-backup.timer; do
        sed "s|$CANONICAL_DIR|$REPO_DIR|g" "deploy/$unit" > "$UNIT_DIR/$unit"
        chmod 644 "$UNIT_DIR/$unit"
    done
    chmod +x scripts/*.sh
    systemctl daemon-reload
    systemctl enable --now oxidized-ai-manager-updater.path >/dev/null
    info "Unidades instaladas para $REPO_DIR; botón \"Actualizar\" activo."
    if [ -f /etc/oxidized-ai-manager-backup.env ]; then
        systemctl enable --now oxidized-ai-manager-backup.timer >/dev/null
        info "Respaldo diario activo (oxidized-ai-manager-backup.timer)."
    else
        warn "Respaldo diario NO activado: falta /etc/oxidized-ai-manager-backup.env con BACKUP_PASSPHRASE."
        printf '      Ver docs/BACKUP_RESTORE.md y luego: sudo systemctl enable --now oxidized-ai-manager-backup.timer\n'
    fi
elif [ ! -f "$UNIT_DIR/oxidized-ai-manager-updater.path" ]; then
    warn "El botón \"Actualizar\" del panel necesita las unidades del anfitrión:"
    printf '      sudo ./install.sh --systemd      (o a mano: docs/UPDATES.md)\n'
fi
info "Usuario: admin"
if [ -n "$ADMIN_PASSWORD" ]; then
    printf '%s[*]%s Clave inicial: %s%s%s\n' "$GRN" "$NC" "$YLW" "$ADMIN_PASSWORD" "$NC"
    info "Cámbiela desde el menú de usuario en su primer ingreso."
else
    info "Clave de admin: la definida en su .env (ADMIN_PASSWORD)."
fi
