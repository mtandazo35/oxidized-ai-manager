# Fase 1 — Fundación

## Alcance implementado

La primera fase crea cuatro servicios persistentes y un inicializador efímero:

- `backend`: FastAPI ejecutado como usuario sin privilegios y con sistema de archivos de solo lectura.
- `postgres`: almacenamiento durable para fases posteriores; no publica puerto al host.
- `redis`: estado temporal persistente; solo es accesible en la red interna de Compose.
- `oxidized`: imagen oficial fijada en `0.37.0`, con interfaz web y volumen persistente.
- `oxidized-init`: prepara el volumen de Oxidized con UID/GID `30000` y un nodo local inactivo de arranque.

No se implementan modelos de dominio, migraciones, inventario, recolección MikroTik, Git de backups ni agentes.

## Health checks

`GET /health/live` confirma que el proceso de la API responde. `GET /health/ready` comprueba en paralelo PostgreSQL (`SELECT 1`), Redis (`PING`) y Oxidized (`/nodes.json`). Devuelve HTTP 503 y el componente degradado si alguna dependencia falla.

Docker Compose impide iniciar el backend hasta que las tres dependencias estén saludables. Los timeouts evitan esperas indefinidas.

## Persistencia y seguridad

Los volúmenes `postgres_data`, `redis_data` y `oxidized_data` sobreviven a reinicios. PostgreSQL y Redis no exponen puertos y ambos exigen credenciales. API y Oxidized escuchan en `127.0.0.1` del host salvo configuración explícita.

El host debe aplicar `vm.overcommit_memory = 1` mediante `/etc/sysctl.d/99-oxidized-ai-manager.conf`; Redis necesita este ajuste para que la persistencia no falle bajo presión de memoria.

Oxidized no puede iniciar con una fuente completamente vacía. Por ello, la Fase 1 incluye `phase1-placeholder` apuntando a `127.0.0.1`, sin credenciales y con `interval: 0`; nunca intenta recolectar una configuración. El inicializador solo instala este marcador mientras `router.db` esté vacío y preservará un inventario real posterior.

La configuración activa el filtrado de secretos soportado por los modelos. Las credenciales reales futuras deben inyectarse mediante un mecanismo de secretos; no deben agregarse a `oxidized/config`, `.env` ni Git.

## Criterio de aceptación

La fase se considera operativa cuando:

1. `docker compose config` termina sin errores.
2. Las pruebas del backend pasan.
3. `docker compose up -d --build` deja PostgreSQL, Redis, Oxidized y backend en estado `healthy`.
4. `/health/ready` responde HTTP 200 con todas las comprobaciones en `true`.
