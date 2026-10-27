#!/usr/bin/env bash
#
# Se ejecuta como ExecStopPost del servicio de actualización: si el servicio no
# llegó a terminar bien, deja constancia y **consume la petición**.
#
# Sin esto, una petición que el servicio no puede atender se queda en el
# directorio, la unidad `.path` vuelve a dispararse en bucle (hasta que systemd
# la frena por repetición) y el panel se queda mostrando "actualizando…" para
# siempre.
#
# Corre como root sobre un directorio que controla el contenedor (uid 10001):
# las mismas precauciones que en apply-update.sh. Nada se abre ni se cambia de
# dueño siguiendo enlaces simbólicos; ver el comentario de set_state() allí.

set -u

CHANNEL_DIR="${CHANNEL_DIR:-$(cd "$(dirname "$0")/.." && pwd)/.update-channel}"
RESULT="${SERVICE_RESULT:-unknown}"

# Éxito: el propio script de actualización ya publicó su estado.
[ "$RESULT" = "success" ] && exit 0

# rm -f sobre un enlace simbólico borra el enlace, nunca su destino.
rm -f "$CHANNEL_DIR/update-request.json"

if [ ! -d "$CHANNEL_DIR" ] || [ -L "$CHANNEL_DIR" ]; then
    exit 0
fi

DETALLE="El servicio del anfitrión no pudo ejecutarse ($RESULT). Compruebe que scripts/apply-update.sh existe y es ejecutable, y revise: journalctl -u oxidized-ai-manager-updater.service"

# Si apply-update.sh ya dejó un estado "error" con el motivo concreto (respaldo
# fallido, vuelta atrás…), se respeta: el mensaje genérico de aquí lo taparía.
# Solo se escribe cuando el estado sigue en "solicitada"/"en-progreso" o no
# existe, es decir, cuando el script murió sin poder explicarse.
python3 -I - "$CHANNEL_DIR" "$DETALLE" <<'PY'
import datetime, json, os, secrets, stat, sys

channel, detail = sys.argv[1:3]
dfd = os.open(channel, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    try:
        fd = os.open("update-status.json",
                     os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                current = json.loads(os.read(fd, 16384).decode("utf-8"))
                if isinstance(current, dict) and current.get("state") == "error":
                    sys.exit(0)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        pass

    # json.dumps escapa el detalle; la escritura es la misma que en
    # apply-update.sh: temporal nuevo con O_EXCL|O_NOFOLLOW, fchown por
    # descriptor y rename() atómico (reemplaza un enlace, no lo sigue).
    payload = json.dumps(
        {
            "state": "error",
            "detail": detail,
            "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "from": "",
            "to": "",
        },
        ensure_ascii=False,
    ).encode("utf-8") + b"\n"
    tmp = ".update-status.%s.tmp" % secrets.token_hex(8)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=dfd)
    try:
        view = memoryview(payload)
        while view:
            view = view[os.write(fd, view):]
        # El contenedor tiene que poder sobreescribir el estado la próxima vez.
        os.fchown(fd, 10001, 10001)
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    try:
        st = os.stat("update-status.json", dir_fd=dfd, follow_symlinks=False)
        if stat.S_ISDIR(st.st_mode):
            os.rmdir("update-status.json", dir_fd=dfd)
    except FileNotFoundError:
        pass
    try:
        os.rename(tmp, "update-status.json", src_dir_fd=dfd, dst_dir_fd=dfd)
    except OSError:
        os.unlink(tmp, dir_fd=dfd)
        raise
finally:
    os.close(dfd)
PY
