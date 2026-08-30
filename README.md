# Oxidized AI Manager

Fundación para una plataforma de respaldo, inventario, auditoría y gestión controlada de MikroTik sobre Oxidized. El repositorio implementa **solo la Fase 1**: PostgreSQL, Redis, Oxidized y una API FastAPI mínima con health checks.

No hay todavía inventario real, agentes IA, acceso a MikroTik ni ejecución de cambios. Oxidized usa un marcador local inactivo para poder iniciar sin routers.

## Requisitos

- Docker Engine 24 o posterior con Docker Compose v2.
- Aproximadamente 2 GB de RAM y 3 GB de espacio libre para el primer build.
- En Raspberry Pi 4: Raspberry Pi OS de 64 bits (`uname -m` debe mostrar `aarch64`).

Las imágenes seleccionadas ofrecen soporte `linux/arm64`; no se fija `platform` para que Docker elija la arquitectura nativa del host.

## Instalación

```bash
cp .env.example .env
nano .env
```

Cambie `POSTGRES_PASSWORD`, `REDIS_PASSWORD` y `APP_SECRET_KEY`. Genere valores aleatorios, por ejemplo:

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
- [Acceso web público](docs/PUBLIC_ACCESS.md)
