# Roadmap

## Fase 1 — Fundación
- Docker Compose
- PostgreSQL
- Redis
- Oxidized
- FastAPI
- health checks
- configuración segura
- documentación de despliegue (Debian 13)

Criterio de salida: stack levanta correctamente y los servicios reportan estado saludable.

## Fase 2 — MikroTik Collector
- inventario
- API/API-SSL
- lectura segura
- soporte inicial RouterOS 7
- diseño compatible RouterOS 6
- manejo de errores/timeouts

Criterio: registrar un MikroTik de laboratorio y obtener inventario.

## Fase 3 — Backups y Git
- integrar Oxidized
- historial
- backup manual
- último backup
- Git
- diff

Criterio: modificar un router de laboratorio y visualizar el cambio.

## Fase 4 — Auditoría
- eventos
- Diff Agent
- Security Agent
- Audit Agent
- BGP Agent
- clasificación de riesgos

Criterio: producir auditorías reproducibles sin modificar el router.

## Fase 5 — Dashboard
- routers
- backups
- cambios
- auditorías
- seguridad
- BGP
- agentes
- alertas

## Fase 6 — Automatización
- jobs
- aprobación
- backup previo
- piloto
- despliegue gradual
- verificación
- rollback
- auditoría completa
