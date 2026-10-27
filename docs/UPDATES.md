# Actualizar desde el panel

En **Ajustes del sistema → Versión y actualizaciones** (solo administrador):
*Buscar actualizaciones* compara lo desplegado con `origin/main`, *Actualizar
ahora* aplica la nueva versión e *Historial de cambios* lista lo ya instalado.

## El número de versión

Sale del archivo `VERSION` de la raíz del repositorio, leído del commit
desplegado con `git show HEAD:VERSION` (el repositorio se monta sin árbol de
trabajo, así que el archivo no está en disco). **Hay que subirlo a mano** al
publicar: cambios de interfaz o correcciones suben el segundo número, un
cambio grande de funcionamiento el primero. Si el archivo no existe —un
despliegue anterior a que se añadiera— el panel muestra el commit corto.

## Por qué el contenedor no se actualiza a sí mismo

Reconstruir el stack exige el socket de Docker. Montarlo dentro del backend
equivale a **darle root del anfitrión** a la aplicación que está publicada tras
el proxy: cualquier fallo en ella dejaría de ser "un panel comprometido" y
pasaría a ser "el servidor comprometido".

Así que el reparto es:

1. El panel llama a `POST /api/system/update` y el backend **solo escribe un
   archivo** de petición en un directorio compartido.
2. Una unidad `.path` de systemd en el anfitrión ve ese archivo y lanza
   `scripts/apply-update.sh`, que **sí** puede reconstruir.
3. El script va escribiendo su estado en el mismo directorio y el panel lo
   consulta para mostrar el avance.

**La petición no lleva URL, ni rama, ni commit.** El remoto y la rama están
fijados en el script del anfitrión. Aunque alguien se hiciera con la cuenta de
administrador del panel, lo único que conseguiría es desplegar el último commit
legítimo del repositorio de siempre — no código arbitrario.

## Qué hace el script, en orden

1. **Respalda** la plataforma (`scripts/backup-system.sh`: PostgreSQL, el
   volumen con `backups.git` y el `.env`). Si el respaldo falla, **no actualiza**.
2. `git fetch` y `git merge --ff-only origin/main`. Si alguien editó archivos en
   el servidor, se detiene en lugar de intentar mezclar y dejar el árbol a medias.
   Con `UPDATE_REQUIRE_SIGNED=1`, antes exige la firma del commit (ver abajo).
3. `docker compose up -d --build`, con los **mismos archivos de Compose que la
   instalación** (con proxy externo, también `deploy/docker-compose.proxy.yml`;
   ver más abajo).
4. `docker compose up -d --force-recreate oxidized`, porque su configuración
   vive en el volumen y el proceso ya arrancado seguiría con la vieja. Se
   **recrea** en lugar de reiniciarse: un `restart` conserva el `/tmp` del
   contenedor, y con él el PID viejo que impide arrancar a Oxidized.
5. Espera hasta 60 s a que `/health/live` responda.
6. **Vuelta atrás**: si el paso 3 o el 5 fallan, vuelve al commit anterior
   (`git reset --keep <anterior>`, que solo toca archivos versionados: el `.env`
   y el canal no lo están), reconstruye y comprueba otra vez. El panel muestra
   `error` con el motivo y «se revirtió a `<commit>`»; si la vuelta atrás
   también falla, lo dice expresamente y hay que intervenir a mano. Las columnas
   que la versión nueva haya añadido a PostgreSQL se quedan; la anterior las
   ignora.

### Mismos archivos de Compose que la instalación

`install.sh --proxy <red>` escribe en `.env`:

```bash
COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.proxy.yml
```

Compose lee esa variable del `.env` del directorio desde el que se ejecuta, así
que `docker compose up -d`, `ps` o `logs` a mano, el botón «Actualizar» y el
respaldo usan siempre el overlay del proxy. Sin ella, cada actualización
recrearía el backend **fuera de la red de NPM** y el panel dejaría de verse.

Instalaciones con proxy anteriores a este cambio (tienen `PROXY_NETWORK` en
`.env` pero no `COMPOSE_FILE`): `apply-update.sh` añade la línea la primera vez
que se ejecuta. Pero la actualización **que trae** este cambio la aplica todavía
el script viejo, sin overlay; por eso, antes de pulsar el botón en esas
instalaciones, añada la línea a mano (o vuelva a ejecutar
`sudo ./install.sh --proxy <red>`), o tras actualizar ejecute una vez
`docker compose up -d` desde el directorio del repositorio.

### Exigir commits firmados (opcional)

Por defecto, quien controle la cuenta de GitHub controla lo que se despliega.
Con `Environment=UPDATE_REQUIRE_SIGNED=1` en
`oxidized-ai-manager-updater.service`, el script ejecuta
`git verify-commit` sobre `origin/main` antes de mezclar y **aborta** si la
firma no es válida o la clave no es de confianza para root:

- firmas GPG: importe las claves públicas de quienes publican en el llavero de
  root (`sudo gpg --import clave.asc` y márquelas de confianza);
- firmas SSH: `sudo git config --global gpg.ssh.allowedSignersFile /etc/oxidized-ai-manager-allowed-signers`
  con una línea `correo clave-pública` por firmante.

Ojo con los *merges* hechos desde la web de GitHub: los firma la clave de
GitHub (`web-flow`), no la suya; o la importa también (y entonces vuelve a
confiar en GitHub) o publica con commits firmados localmente. Solo se verifica
el commit de la punta: un firmante de confianza responde de lo que incluye.

## Ningún dato se pierde

- **No se usa `docker compose down`**, y mucho menos `down -v`: los contenedores
  se recrean en sitio y los volúmenes (`postgres_data`, `oxidized_data`,
  `redis_data`) no se tocan.
- El esquema de PostgreSQL se migra solo al arrancar y todo es
  `CREATE TABLE ... IF NOT EXISTS` / `ADD COLUMN ... IF NOT EXISTS`: nunca borra
  ni recrea tablas.
- El `.env` está en `.gitignore`, así que `git pull` no lo sobreescribe.
- Y aun así hay un respaldo previo en `/root/backups/` antes de cada intento.

## Instalación en el anfitrión

La ruta canónica del repositorio es **`/opt/oxidized-ai-manager`**: es la que
traen todas las unidades de `deploy/` (actualizador y respaldo) y la que usa
esta documentación.

Lo más simple, desde el repositorio y en cualquier ruta:

```bash
sudo ./install.sh --systemd
```

Copia las cuatro unidades a `/etc/systemd/system/` **sustituyendo la ruta por la
real** del repositorio, habilita `oxidized-ai-manager-updater.path` y, si existe
`/etc/oxidized-ai-manager-backup.env`, también el temporizador de respaldo. Es
idempotente: puede repetirse tras cada cambio de las unidades.

A mano, una vez, en el servidor:

```bash
cd /opt/oxidized-ai-manager

# Directorio del canal: debe pertenecer al usuario del contenedor (uid 10001)
mkdir -p .update-channel && chown -R 10001:10001 .update-channel && chmod 700 .update-channel

sudo cp deploy/oxidized-ai-manager-updater.path    /etc/systemd/system/
sudo cp deploy/oxidized-ai-manager-updater.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oxidized-ai-manager-updater.path
```

Los scripts deben quedar ejecutables (`chmod +x scripts/*.sh`). El repositorio
ya guarda ese permiso, pero si clona desde Windows conviene comprobarlo: sin él
systemd falla con `203/EXEC` y el panel muestra el error correspondiente.

Si el repositorio está en otra ruta, use `install.sh --systemd`, o sustituya
la ruta al copiar (afecta a `WorkingDirectory`, `PROJECT_DIR`, `CHANNEL_DIR`,
`ExecStart`, `ExecStopPost` y la ruta vigilada en el `.path`):

```bash
RUTA="$(pwd -P)"
for u in deploy/oxidized-ai-manager-*.path deploy/oxidized-ai-manager-*.service deploy/oxidized-ai-manager-*.timer; do
    sed "s|/opt/oxidized-ai-manager|$RUTA|g" "$u" | sudo tee "/etc/systemd/system/$(basename "$u")" >/dev/null
done
sudo systemctl daemon-reload
```

**Instalaciones anteriores en `/root/oxidized-ai-manager`**: las unidades de
`deploy/` decían antes `/root/...` y ahora dicen `/opt/...`. Si vuelve a
copiarlas tal cual, el actualizador apuntará a una ruta que no existe; use
`install.sh --systemd` o el `sed` anterior.

`install.sh` crea el directorio del canal con el dueño correcto; sin
`--systemd` no toca `/etc/systemd`.

## Garantías frente al contenedor

El directorio del canal pertenece al contenedor (uid 10001) y los scripts del
anfitrión corren como root, así que todo lo que hay dentro se trata como
hostil:

- la petición solo se lee si es un **archivo regular** (no un enlace simbólico,
  FIFO ni dispositivo), con `O_NOFOLLOW` y como mucho **4 KiB**; si no, se
  borra y el estado pasa a `error`;
- el estado se escribe en un temporal nuevo (`O_EXCL|O_NOFOLLOW`), se le cambia
  el dueño **por descriptor** (`fchown`, nunca por nombre) y se coloca con
  `rename()`, que reemplaza un enlace plantado en lugar de seguirlo;
- el detalle se serializa con `json.dumps`, nunca interpolado a mano;
- si `apply-update.sh` ya dejó un `error` con su motivo, `update-failed.sh` no
  lo tapa con el mensaje genérico.

## Diagnóstico

| Síntoma | Causa habitual |
| --- | --- |
| «El canal de actualización no está montado» | falta el directorio o el volumen en compose |
| La petición se queda en «solicitada» | la unidad `.path` no está activa: `systemctl status oxidized-ai-manager-updater.path` |
| «no se pudo escribir la petición» | el directorio no pertenece a uid 10001 |
| «El servicio del anfitrión no pudo ejecutarse» | `scripts/apply-update.sh` sin permiso de ejecución (`chmod +x`) |
| «sin comprobar» en la insignia | el backend no puede leer `/repo/.git` o no hay salida a internet |
| «El repositorio local tiene cambios propios» | hay ediciones a mano en el servidor: `git status` y resolverlas |
| «… Se revirtió a `<commit>`» | la versión nueva no construyó o no arrancó; sigue la anterior: `docker compose logs --tail=100` |
| «La vuelta atrás … TAMBIÉN falló» | el stack puede estar caído: `git log -1`, `docker compose ps`, y el respaldo previo en `/root/backups/` |
| «no tiene una firma válida de confianza» | `UPDATE_REQUIRE_SIGNED=1` y el commit no está firmado por una clave del llavero de root |
| El panel deja de verse tras actualizar (con proxy) | falta `COMPOSE_FILE` en `.env`: ver «Mismos archivos de Compose» |

Registro de lo que hizo el anfitrión:

```bash
journalctl -u oxidized-ai-manager-updater.service -n 50
```

## Actualizar a mano

El botón no es obligatorio; sigue funcionando lo de siempre, **desde el
directorio del repositorio** (para que Compose lea el `.env` y, con proxy, su
`COMPOSE_FILE`):

```bash
cd /opt/oxidized-ai-manager
./scripts/backup-system.sh /root/backups
git pull --ff-only origin main
docker compose up -d --build
docker compose up -d --force-recreate oxidized   # recrear, no `restart`
```

Si su `.env` tiene `PROXY_NETWORK` pero no `COMPOSE_FILE` (instalación con
proxy anterior), añádalo antes o pase el overlay explícitamente:
`docker compose -f docker-compose.yml -f deploy/docker-compose.proxy.yml up -d --build`.
