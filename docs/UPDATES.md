# Actualizar desde el panel

En **Configuración → Versión y actualizaciones** (solo administrador) hay dos
botones: *Buscar actualizaciones*, que compara el commit desplegado con
`origin/main`, y *Actualizar ahora*, que aplica la nueva versión.

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
3. `docker compose up -d --build`.
4. `docker compose restart oxidized`, porque su configuración vive en el volumen
   y el proceso ya arrancado seguiría con la vieja.
5. Espera hasta 60 s a que `/health/live` responda. Si no responde, deja el
   estado en `error` y apunta al respaldo previo.

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

Una vez, en el servidor:

```bash
cd /opt/oxidized-ai-manager   # o /root/oxidized-ai-manager

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

Si el `PROJECT_DIR` no es `/root/oxidized-ai-manager`, ajústelo en el
`.service` y la ruta vigilada en el `.path`.

`install.sh` ya crea el directorio con el dueño correcto; las unidades de
systemd hay que copiarlas a mano porque el instalador no toca `/etc/systemd`.

## Diagnóstico

| Síntoma | Causa habitual |
| --- | --- |
| «El canal de actualización no está montado» | falta el directorio o el volumen en compose |
| La petición se queda en «solicitada» | la unidad `.path` no está activa: `systemctl status oxidized-ai-manager-updater.path` |
| «no se pudo escribir la petición» | el directorio no pertenece a uid 10001 |
| «El servicio del anfitrión no pudo ejecutarse» | `scripts/apply-update.sh` sin permiso de ejecución (`chmod +x`) |
| «sin comprobar» en la insignia | el backend no puede leer `/repo/.git` o no hay salida a internet |
| «El repositorio local tiene cambios propios» | hay ediciones a mano en el servidor: `git status` y resolverlas |

Registro de lo que hizo el anfitrión:

```bash
journalctl -u oxidized-ai-manager-updater.service -n 50
```

## Actualizar a mano

El botón no es obligatorio; sigue funcionando lo de siempre:

```bash
cd /root/oxidized-ai-manager
./scripts/backup-system.sh /root/backups
git pull --ff-only origin main
docker compose up -d --build
docker compose restart oxidized
```
