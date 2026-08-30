# Fase 2 — Inventario y fuente HTTP (parcial)

## Alcance implementado

Esta entrega moderniza la integración con Oxidized reemplazando el CSV estático
(`router.db`) por un inventario en PostgreSQL administrado vía API:

- Tabla `devices` creada automáticamente al arrancar el backend (sin migraciones aún).
- CRUD REST en `/api/devices` (crear, listar, consultar, actualizar, eliminar).
- Endpoint `/api/oxidized/nodes` que Oxidized consume mediante `source: http`.
- Autenticación del endpoint de nodos con el header `X-Oxidized-Token`
  (`OXIDIZED_SOURCE_TOKEN` en `.env`; `oxidized-init` lo inyecta en la config).

Queda pendiente de la Fase 2: el MikroTik Collector (inventario rico vía API-SSL,
modelo/serial/versión/uptime) y el manejo de errores/timeouts contra RouterOS.

## Diseño

- Oxidized permanece sin parches: solo cambia su adaptador `source` de `csv` a
  `http`, un punto de extensión oficial. La config sigue siendo un seed en
  `oxidized/config`; el inicializador sustituye `__OXIDIZED_SOURCE_TOKEN__`.
- Orden de arranque: PostgreSQL/Redis → backend (healthcheck `/health/live`) →
  Oxidized. Se eliminó la dependencia del backend hacia Oxidized para evitar el
  ciclo; `/health/ready` sigue reportando el estado de las tres dependencias.
- Si el inventario está vacío, `/api/oxidized/nodes` devuelve el marcador
  `phase1-placeholder` porque Oxidized aborta con cero nodos.
- Las respuestas públicas del CRUD nunca incluyen `password`; solo el endpoint
  de nodos (autenticado, red interna) entrega credenciales.

## Uso

```bash
# Alta de un router (desde el host)
curl -X POST http://127.0.0.1:8000/api/devices \
  -H 'Content-Type: application/json' \
  -d '{"name":"rb-lab-01","address":"192.0.2.10","username":"backup","password":"CAMBIAR"}'

# Listar inventario (sin contraseñas)
curl http://127.0.0.1:8000/api/devices

# Deshabilitar temporalmente un equipo
curl -X PATCH http://127.0.0.1:8000/api/devices/1 -H 'Content-Type: application/json' -d '{"enabled":false}'

# Recargar nodos en Oxidized sin reiniciar
curl http://127.0.0.1:8888/reload
```

## Seguridad

- Las credenciales de equipos se almacenan en PostgreSQL (no expuesto fuera de
  la red interna de Compose). El cifrado en reposo / gestor de secretos queda
  para una fase posterior; no volver al CSV en Git.
- Use cuentas RouterOS de solo lectura para los respaldos.
- `OXIDIZED_SOURCE_TOKEN` debe ser hexadecimal largo (`openssl rand -hex 32`).

## Criterio de aceptación

1. Las pruebas del backend pasan (`pytest -q`).
2. `docker compose config` termina sin errores.
3. Con el stack arriba, `GET /api/oxidized/nodes` con token devuelve el
   placeholder (inventario vacío) y Oxidized queda `healthy`.
4. Tras registrar un dispositivo y llamar a `/reload`, Oxidized lo muestra en
   `/nodes.json`.
