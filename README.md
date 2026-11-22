# Oxidized AI Manager

Fundación para una plataforma de respaldo, inventario, auditoría y gestión controlada de MikroTik sobre Oxidized. El repositorio implementa la **Fase 1** (PostgreSQL, Redis, Oxidized, API FastAPI con health checks) y el **inventario de la Fase 2**: los routers se registran vía API en PostgreSQL y Oxidized los lee con `source: http`, sin CSV estático.

La **Fase 4** añade la auditoría de configuraciones: un motor de reglas determinista sobre los respaldos ya versionados (`docs/PHASE4.md`), con pestaña propia en el panel.

No hay ejecución de cambios en los routers: la plataforma es read-only de punta a punta y la auditoría no abre sesión contra ningún equipo. Con el inventario vacío, la API entrega un marcador inactivo para que Oxidized pueda iniciar sin routers.

## Requisitos

- Debian 13 (trixie) o similar, x86_64. Docker Engine 24 o posterior con Docker Compose v2.
- Aproximadamente 2 GB de RAM y 3 GB de espacio libre para el primer build.

Las imágenes seleccionadas son multi-arch (amd64/arm64); no se fija `platform` para que Docker elija la arquitectura nativa del host.

## ⚡ Instalación rápida (one-liner)

En cualquier VPS Debian/Ubuntu recién creado (el instalador instala Docker y las
demás dependencias por sí mismo):

```bash
sudo apt-get update && sudo apt-get install -y git && \
sudo git clone https://github.com/mtandazo35/oxidized-ai-manager.git /opt/oxidized-ai-manager && \
cd /opt/oxidized-ai-manager && sudo ./install.sh --systemd
```

`/opt/oxidized-ai-manager` es la ruta canónica (la de las unidades de
`deploy/` y la documentación). `--systemd` instala además las unidades del
botón «Actualizar» y del respaldo diario con la ruta real del repositorio.

El script instala Docker/Compose si faltan, genera `.env` con secretos
aleatorios, aplica el ajuste de kernel para Redis, levanta el stack e imprime la
clave inicial de `admin`. Es idempotente: si vuelve a ejecutarlo, conserva el
`.env` existente.

Por defecto el panel queda **solo en `127.0.0.1`** del host. Para exponerlo se
usa un **Nginx Proxy Manager externo** (no forma parte de este repositorio):
el backend se conecta a su red Docker y no publica ningún puerto en la LAN.

```bash
docker network ls                        # localice la red de NPM
sudo ./install.sh --proxy npm_default
```

Después cree el proxy host en NPM apuntando a `backend:8000` (esquema `http`).
Detalles, certificado sin dominio público y listas de acceso en
[docs/PUBLIC_ACCESS.md](docs/PUBLIC_ACCESS.md).

## Instalación manual

```bash
cp .env.example .env
nano .env
```

Cambie `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `APP_SECRET_KEY`, `OXIDIZED_SOURCE_TOKEN` y `ADMIN_PASSWORD`. Genere valores aleatorios, por ejemplo:

```bash
openssl rand -hex 32
```

Valide y levante el stack:

```bash
echo 'vm.overcommit_memory = 1' | sudo tee /etc/sysctl.d/99-oxidized-ai-manager.conf
sudo sysctl --load /etc/sysctl.d/99-oxidized-ai-manager.conf
docker compose config
docker compose up -d --build
docker compose ps
```

Todos los servicios deben aparecer como `healthy`; `oxidized-init` debe terminar con estado `Exited (0)`.

## Verificación

```bash
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
curl http://127.0.0.1:8888/nodes.json
```

La documentación interactiva de FastAPI está en `http://127.0.0.1:8000/docs`. Los puertos se enlazan a localhost por defecto. Para acceder desde la LAN, configure las variables `*_BIND_ADDRESS` con la IP local concreta del servidor; evite exponer estos servicios a Internet.

## Autenticación

La API exige login. El usuario inicial se crea con `ADMIN_USERNAME`/`ADMIN_PASSWORD` del `.env` solo en el primer arranque; después la clave vive en PostgreSQL y se cambia por API (el valor del `.env` deja de importar):

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -d 'username=admin' -d 'password=SU_CLAVE' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -X POST http://127.0.0.1:8000/api/auth/change-password \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"current_password":"SU_CLAVE","new_password":"CLAVE_NUEVA_LARGA"}'
```

En `http://127.0.0.1:8000/` hay un **panel web** con el mismo login: estado de respaldos, alta/baja de routers y cambio de clave. En `/docs` el botón **Authorize** permite usar la API de forma interactiva.

El panel abre siempre en **Routers**. Esa tabla tiene nueve columnas de datos, que en un portátil obligan a leer en horizontal, así que el menú **Columnas** deja esconder las que no interesen —quien revisa respaldos no suele mirar Puerto ni Usuario—. La elección se guarda en el navegador de cada quien, no en el servidor, así que no cambia lo que ven los demás; la columna de botones no se puede ocultar.

## Inventario de routers

Los equipos se administran por API (autenticada) y Oxidized los recibe automáticamente:

```bash
curl -X POST http://127.0.0.1:8000/api/devices \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"rb-lab-01","address":"192.0.2.10","username":"backup","password":"CAMBIAR"}'

curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/devices   # listado sin contraseñas
curl http://127.0.0.1:8888/reload                                          # recarga de nodos en Oxidized
```

### Varias credenciales por equipo

Un router puede tener más de un par usuario/clave. En **Editar** (y en **Agregar**) la primera fila es la credencial principal y el botón **+** añade las siguientes, hasta ocho adicionales. El orden de las filas es el orden en que se prueban.

- **Validar credenciales** abre una sesión SSH con cada par, muestra cuál entra y deja esa como activa. Oxidized recibe siempre la credencial activa.
- **Cambio automático:** si un respaldo falla, el panel prueba el resto de credenciales de ese equipo y, si otra entra, la activa para el siguiente intento. Hay una espera de cinco minutos por equipo entre dos comprobaciones para no llenar de intentos fallidos el servidor de autenticación.
- Las claves se guardan cifradas y la API nunca las devuelve: el listado solo trae los nombres de usuario. Dejar una clave en blanco al editar conserva la que ya estaba.
- La sesión SSH usa únicamente el método `password`, tanto al validar como al respaldar. Con los métodos por defecto de la librería, el router recibe antes un intento sin clave y, si autentica contra RADIUS, lo registra como un acceso rechazado en cada respaldo.

### Omitir respaldos

Un equipo puede seguir en el inventario sin respaldarse: al marcarlo como **omitido** deja de enviarse a Oxidized y conserva su historial. Sirve para un router en mantenimiento o para uno cuyo export se cuelga y no debe ocupar la cola. El estado se ve en la columna correspondiente de la tabla de Routers.

### Cambios masivos

**Sistema → Cambios masivos** aplica el mismo cambio a muchos equipos de una vez:

1. **Elegir equipos:** todos (casilla **Seleccionar todos**), una empresa entera o routers sueltos. El desplegable de empresa filtra la lista.
2. **Elegir qué cambiar**, marcando solo lo que se quiere tocar: usuario, clave, puerto SSH, omitir respaldos, y añadir o quitar credenciales adicionales.
3. **Aplicar.** Lo que no se marca no se modifica.

> **Cuidado con la clave.** Un cambio masivo de clave sobrescribe la de todos los equipos elegidos, y el panel no puede saber si los routers ya aceptan la nueva: si se aplica antes de cambiarla en los equipos (o en el servidor RADIUS), todos fallarán la autenticación en el siguiente respaldo. Conviene revisar también que el navegador no haya autocompletado el campo de clave. Para deshacerlo hace falta un respaldo de la base ([docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md)).

Por API es `POST /api/devices/bulk-update`, con `scope` (`all`, `group` o `devices`) y solo los campos a cambiar.

## Estado de respaldos

Cada respaldo queda versionado en Git (volumen de Oxidized) y notificado al backend:

```bash
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/backups/status   # último respaldo por router
curl -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8000/api/backups/events?node=rb-lab-01'
```

Detalles y criterios de aceptación en [docs/PHASE2.md](docs/PHASE2.md).

### Cómo leer la pantalla

Las tarjetas **Con fallo** y **Pendientes** se pueden pulsar: filtran la tabla para ver qué equipos son.

| Estado | Qué significa |
|---|---|
| Respaldo correcto | El último intento terminó bien y quedó guardado. |
| Con fallo | El último intento falló. La insignia roja «✖ N» cuenta los fallos seguidos; el filtro **fallan seguido** deja solo los que repiten. |
| Pendientes | Equipos del inventario que todavía no tienen resultado: están en cola, respaldándose ahora o nunca se han intentado. |
| Fuera de inventario | Hay respaldos guardados con ese nombre, pero el equipo ya no existe en el inventario (se borró o se renombró). |

Un respaldo puede figurar como correcto y traer un **aviso de integridad**: el panel compara cada respaldo con el anterior y avisa si desaparecieron secciones enteras. Suele indicar que el propio router no pudo exportarlas; en ese caso el archivo termina con una línea `#error exporting ...` escrita por RouterOS.

### Limpieza de eventos viejos

Cada respaldo deja un par de eventos, así que con respaldos cada hora la tabla suma varios miles de filas al día. Una vez al día el panel borra los eventos de más de `BACKUP_EVENTS_RETENTION_DAYS` (365 por defecto, en `.env`).

De cada equipo se conservan siempre, por viejos que sean, su último evento, su último éxito y el último que trae commit: un router que lleva más de un año sin respaldar sigue mostrando cuándo fue su último respaldo bueno y permite descargarlo. Esto solo recorta el registro de eventos; las configuraciones guardadas en Git no se tocan.

### Respaldar a mano

**Sistema → Respaldos programados** fija cada cuántos minutos se respalda y permite lanzar un respaldo de todos, de una empresa o de los routers marcados (con **Seleccionar todos** para la lista visible).

### Ajustes de Oxidized que importan

Están en [oxidized/config](oxidized/config), con el motivo de cada uno comentado:

| Ajuste | Valor | Por qué |
|---|---|---|
| `use_max_threads` | `true` | Sin él Oxidized calcula cuántos respaldos corre a la vez a partir de `interval`, y como aquí los respaldos los dispara el panel (intervalo de un año) el resultado es uno solo: la flota va en fila y un equipo lento bloquea a todos. |
| `threads` | `20` | Respaldos simultáneos. |
| `timelimit` | `900` | Techo de una sesión completa. Hay cores cuyo `/export` tarda varios minutos. |
| `timeout` | `30` | Límite de conexión: un equipo inalcanzable falla aquí. |
| `vars.auth_methods` | `password` | Un solo intento de autenticación por respaldo. |

Para aplicar un cambio de ese archivo: `docker compose up -d oxidized-init && docker compose restart oxidized`. **Reiniciar Oxidized relanza el respaldo de todo el inventario**, con un inicio de sesión por equipo.

### Si un equipo no respalda

| Síntoma en el registro de Oxidized | Causa habitual |
|---|---|
| `Net::SSH::AuthenticationFailed` | Usuario o clave que el router no acepta. Usar **Validar credenciales**. |
| `status no_connection` sin más detalle | El puerto SSH no responde o rechaza la conexión: servicio apagado, otro puerto, firewall o equipo caído. |
| `Job timelimit reached` | El export no terminó dentro de `timelimit`. Si se repite, el problema está en el router. |
| Muchos equipos en **Pendientes** a la vez | Cola en marcha tras un «respaldar todos» o un reinicio; se vacía sola. |

Oxidized no permite cancelar un respaldo en curso: un trabajo colgado termina cuando salta su `timelimit`. Mientras tanto no frena a los demás, y el equipo se puede marcar como omitido.

## Auditoría de configuraciones

Reglas deterministas sobre el último respaldo de cada equipo (sin tocar el router):

```bash
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/audit/summary          # riesgo por router
curl -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8000/api/audit/node?node=rb-lab-01'
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/audit/rules            # catálogo de reglas
```

En el panel están en la pestaña **Auditoría**, con los mismos filtros que la lista de routers: buscador, empresa y agrupado (por empresa o por riesgo). Los totales de la derecha cuentan lo que se está viendo, y entre paréntesis el inventario completo cuando hay un filtro puesto. Detalles, reglas y criterios en
[docs/PHASE4.md](docs/PHASE4.md).

> Los respaldos incluyen las claves de los equipos para ser restaurables. La
> auditoría censura esos valores en su evidencia, pero el repositorio
> `backups.git` es material sensible: lea [docs/SECURITY.md](docs/SECURITY.md).

Consulte diagnósticos con `docker compose logs --tail=100 <servicio>` y detenga el stack con `docker compose down`. No use `docker compose down -v` salvo que pretenda borrar todos los datos locales.

## Acceso de clientes (multi-tenant)

Cada cuenta pertenece a una empresa y **solo ve los equipos de esa empresa**,
con cuatro roles (`admin`, `operador`, `auditor`, `lector`). Las cuentas se
gestionan en la pestaña **Usuarios** del panel o por API:

```bash
curl -X POST http://127.0.0.1:8000/api/users \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"username":"cliente-acme","password":"...","role":"lector","group_name":"ACME"}'
```

El filtrado se aplica en la API, endpoint por endpoint, no en el panel.
Detalles y matriz de permisos en [docs/MULTITENANT.md](docs/MULTITENANT.md).

## Equipos soportados

Por defecto **MikroTik RouterOS**, que cubre la 6 y la 7 con el mismo modelo
(Oxidized detecta la versión). Al dar de alta un equipo se elige la plataforma;
el desplegable trae las más habituales —Huawei VRP y OLT SmartAX, ZTE OLT,
BDCOM, Cisco IOS, EdgeOS…— y admite escribir cualquier otro de los ~200 modelos
de Oxidized.

La cuenta de respaldo del MikroTik necesita `policy=ssh,read,sensitive`; sin
`sensitive` el `/export` sale censurado y el respaldo no sirve para restaurar.

RouterOS puede además entregar un respaldo incompleto sin que nada dé error: el
servidor SSH del router corta el export cuando pasa de 128 KiB y hay latencia,
y cierra la conexión limpiamente. Está corregido aquí, y cada respaldo que entra
se mide y se compara con el anterior para que un archivo a medias no vuelva a
quedar en verde. Merece la pena leerlo antes de dudar de un respaldo:
[docs/ROUTEROS_RESPALDO_COMPLETO.md](docs/ROUTEROS_RESPALDO_COMPLETO.md).

El nombre del equipo se ajusta solo: «Router Sucursal Norte» se guarda como
«Router-Sucursal-Norte», porque ese nombre es el del archivo dentro del
repositorio de respaldos.

## Bitácora y control de acceso

En **Ajustes del sistema** (solo administrador): quién hizo qué, cuándo y desde
qué IP; la lista de redes autorizadas para iniciar sesión; y los bloqueos
activos por IP y por cuenta, con su botón para levantarlos. Todo persiste en
PostgreSQL y se purga solo a los 90 días. Detalles en
[docs/ACTIVITY_LOG.md](docs/ACTIVITY_LOG.md).

## Actualizar desde el panel

En **Configuración → Versión y actualizaciones** (solo administrador) se
compara el commit desplegado con `origin/main` y se aplica la nueva versión con
un botón. El contenedor **no** se reconstruye a sí mismo: deja una petición y
la aplica una unidad de systemd del anfitrión, que respalda antes y nunca toca
los volúmenes; si la versión nueva no construye o no arranca, vuelve sola a la
anterior. La unidad se instala con `sudo ./install.sh --systemd`; detalles en
[docs/UPDATES.md](docs/UPDATES.md).

## Respaldo de la plataforma

Oxidized respalda los routers; esto respalda **la plataforma** (PostgreSQL, el
volumen con `backups.git` y el `.env` del que depende el descifrado):

```bash
BACKUP_PASSPHRASE='...' ./scripts/backup-system.sh /root/backups
```

Para dejarlo diario hay units de systemd en `deploy/`. Procedimiento completo y
restauración verificada en [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md).

## Migrar a otra instalación

Para mudar el inventario a un servidor donde ya está instalado este mismo sistema, sin copiar archivos a mano: **Sistema → Migrar a otra instalación** (solo administrador).

1. **En el servidor de origen**, escribir una frase de cifrado (mínimo 12 caracteres) y pulsar **Descargar base de datos**. Baja un archivo `oxidized-ai-manager-migracion-<fecha>.json`.
2. **En el servidor nuevo**, abrir el mismo apartado, elegir ese archivo, escribir la misma frase y pulsar **Cargar base de datos**.

| Qué viaja | Qué no viaja |
|---|---|
| Equipos con todas sus credenciales, puerto, empresa, intervalo y estado de omitido | El historial de configuraciones de los routers (`backups.git`) |
| Cuentas de acceso, con su rol, empresa y contraseña | Los eventos de respaldo y la bitácora |
| Ajustes: intervalos, Telegram, Git remoto, lista de IPs | Bloqueos de IP y de cuenta vigentes |

Lo que conviene saber antes de usarlo:

- **Por qué no es un volcado de PostgreSQL.** Las claves de los routers se guardan cifradas con la `APP_SECRET_KEY` de cada instalación; un volcado llevado a otro servidor trae claves que allí no se pueden leer. El paquete se descifra en origen, se sella con la frase y se vuelve a cifrar en destino con su propia llave.
- **Sin la frase el archivo no se abre**, ni siquiera en el servidor que lo generó. No se guarda en ningún sitio.
- **Al importar no se pierde nada de lo que ya hay.** Los equipos que faltan se crean y los que coinciden por nombre se actualizan. Las cuentas que ya existen en destino no se tocan, así que quien importa conserva su contraseña.
- **Dos ajustes llegan apagados** y se encienden a mano tras revisarlos: el envío al Git remoto (para que dos instalaciones vivas no empujen al mismo repositorio) y la lista de IPs permitidas para ingresar (es la del servidor de origen y podría dejar fuera a quien importa).
- **El servidor nuevo respalda todo el inventario** en cuanto recibe los equipos, con un inicio de sesión por router.

Si además hay que llevarse el historial de configuraciones, el camino es el respaldo completo de la sección anterior.

Por API: `POST /api/system/migration/export` con `{"frase": "..."}` y `POST /api/system/migration/import` con `{"frase": "...", "paquete": {...}, "importar_ajustes": true}`.

## Monitoreo

| URL | Qué vigila |
| --- | --- |
| `/health/live` | el backend responde |
| `/health/ready` | PostgreSQL, Redis y Oxidized alcanzables |
| `/health/backups` | 503 si algún equipo lleva sin respaldo más de 3× su intervalo |

Los tres son públicos y valen para cualquier monitor; `/health/backups` devuelve solo
recuentos, nunca nombres de equipos.

## Pruebas del backend

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/pytest -q
```

Las pruebas usan dobles para las dependencias; no necesitan contenedores ni routers reales.

## Diseño del panel (Tailwind CSS)

Los estilos del panel se escriben en `panel/panel.css` con **Tailwind CSS 4** y se
compilan dentro del propio `backend/app/static/index.html`, entre los marcadores
`tailwind:begin` y `tailwind:end`. La CSP no admite CDN ni hojas de terceros, así
que el CSS generado viaja dentro del archivo y **el despliegue no necesita Node**:
solo hace falta para cambiar el diseño.

```bash
cd panel
npm install
npm run build     # compila e incrusta el CSS (o `npm run watch` mientras se edita)
```

Para ver el panel sin Docker ni PostgreSQL hay un servidor de demostración con
datos de ejemplo en memoria y un repositorio de respaldos temporal:

```bash
.venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/python panel/dev_server.py      # http://127.0.0.1:8000 — admin / demo-admin-123
```

Es solo para desarrollo: no guarda nada y la clave es fija.

## Documentación

- [Contexto del proyecto](docs/PROJECT_CONTEXT.md)
- [Arquitectura](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Fase 4 — Auditoría](docs/PHASE4.md)
- [Multi-tenant y roles](docs/MULTITENANT.md)
- [Bitácora y control de acceso](docs/ACTIVITY_LOG.md)
- [Exposición con Nginx Proxy Manager](docs/PUBLIC_ACCESS.md)
- [Respaldo y restauración](docs/BACKUP_RESTORE.md)
- [Respaldos de RouterOS completos (truncamiento y secretos)](docs/ROUTEROS_RESPALDO_COMPLETO.md)
- [Actualizaciones desde el panel](docs/UPDATES.md)
- [Seguridad](docs/SECURITY.md)
- [QA del proyecto (pendientes)](docs/QA_README.md)
- [Detalles de la Fase 1](docs/PHASE1.md)
- [Detalles de la Fase 2 (inventario)](docs/PHASE2.md)
- [Detalles de la Fase 3 (respaldos Git y eventos)](docs/PHASE3.md)
- [Acceso web público](docs/PUBLIC_ACCESS.md)
