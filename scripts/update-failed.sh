#!/usr/bin/env bash
#
# Se ejecuta como ExecStopPost del servicio de actualización: si el servicio no
# llegó a terminar bien, deja constancia y **consume la petición**.
#
# Sin esto, una petición que el servicio no puede atender se queda en el
# directorio, la unidad `.path` vuelve a dispararse en bucle (hasta que systemd
# la frena por repetición) y el panel se queda mostrando "actualizando…" para
# siempre.

set -u

CHANNEL_DIR="${CHANNEL_DIR:-/root/oxidized-ai-manager/.update-channel}"
RESULT="${SERVICE_RESULT:-unknown}"

# Éxito: el propio script de actualización ya publicó su estado.
[ "$RESULT" = "success" ] && exit 0

rm -f "$CHANNEL_DIR/update-request.json"

DETALLE="El servicio del anfitrión no pudo ejecutarse ($RESULT). Compruebe que scripts/apply-update.sh existe y es ejecutable, y revise: journalctl -u oxidized-ai-manager-updater.service"
TMP="$CHANNEL_DIR/update-status.json.tmp"
printf '{"state":"error","detail":"%s","at":"%s","from":"","to":""}\n' \
    "$DETALLE" "$(date -Is)" > "$TMP"
mv -f "$TMP" "$CHANNEL_DIR/update-status.json"
# El contenedor (uid 10001) tiene que poder sobreescribir el estado la próxima vez.
chown 10001:10001 "$CHANNEL_DIR/update-status.json" 2>/dev/null || true
