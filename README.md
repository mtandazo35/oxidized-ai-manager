# Oxidized AI Manager

Fundación para una plataforma de respaldo, inventario, auditoría y gestión controlada de MikroTik sobre Oxidized. El repositorio implementa la **Fase 1** (PostgreSQL, Redis, Oxidized, API FastAPI con health checks) y el **inventario de la Fase 2**: los routers se registran vía API en PostgreSQL y Oxidized los lee con `source: http`, sin CSV estático.

No hay todavía agentes IA ni ejecución de cambios. Con el inventario vacío, la API entrega un marcador inactivo para que Oxidized pueda iniciar sin routers.

## Requisitos

- Debian 13 (trixie) o similar, x86_64. Docker Engine 24 o posterior con Docker Compose v2.
- Aproximadamente 2 GB de RAM y 3 GB de espacio libre para el primer build.

Las imágenes seleccionadas son multi-arch (amd64/arm64); no se fija `platform` para que Docker elija la arquitectura nativa del host.

## Instalación

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

En `http://127.0.0.1:8000/docs` el botón **Authorize** permite iniciar sesión de forma interactiva.

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

## Estado de respaldos

Cada respaldo queda versionado en Git (volumen de Oxidized) y notificado al backend:

```bash
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/backups/status   # último respaldo por router
curl -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8000/api/backups/events?node=rb-lab-01'
```

Detalles y criterios de aceptación en [docs/PHASE2.md](docs/PHASE2.md).

Consulte diagnósticos con `docker compose logs --tail=100 <servicio>` y detenga el stack con `docker compose down`. No use `docker compose down -v` salvo que pretenda borrar todos los datos locales.

## Pruebas del backend

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/pytest -q
```

Las pruebas usan dobles para las dependencias; no necesitan contenedores ni routers reales.

## Documentación

- [Contexto del proyecto](docs/PROJECT_CONTEXT.md)
- [Arquitectura](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Seguridad](docs/SECURITY.md)
- [Detalles de la Fase 1](docs/PHASE1.md)
- [Detalles de la Fase 2 (inventario)](docs/PHASE2.md)
- [Detalles de la Fase 3 (respaldos Git y eventos)](docs/PHASE3.md)
- [Acceso web público](docs/PUBLIC_ACCESS.md)
