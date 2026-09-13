# Roadmap

## Fase 1 — Fundación ✅
- Docker Compose ✅
- PostgreSQL ✅
- Redis ✅ (desplegado; todavía no se usa como cola: ver Fase 6)
- Oxidized ✅
- FastAPI ✅
- health checks ✅ (`/health/live`, `/health/ready`, `/health/backups`)
- configuración segura ✅
- documentación de despliegue (Debian 13) ✅

Criterio de salida: stack levanta correctamente y los servicios reportan estado
saludable. **Cumplido.**

## Fase 2 — MikroTik Collector (parcial)
- inventario ✅ (tabla `devices`, CRUD, carga masiva CSV/XLSX, grupos)
- lectura segura ✅ (claves cifradas en reposo; `source: http` hacia Oxidized)
- soporte inicial RouterOS 7 ✅
- API/API-SSL — pendiente: hoy la recolección es por SSH vía Oxidized
- prueba de conexión antes de guardar un equipo — pendiente
- diseño compatible RouterOS 6 — pendiente
- manejo de errores/timeouts — parcial (eventos `node_fail`, sin reintentos)

Criterio: registrar un MikroTik de laboratorio y obtener inventario.
**Cumplido** con equipos reales.

## Fase 3 — Backups y Git ✅
- integrar Oxidized ✅
- historial ✅
- backup manual ✅ (por equipo y masivo por grupo/selección)
- último backup ✅
- Git ✅ (local + push opcional a remoto, URL cifrada en reposo)
- diff ✅ (por versión, con descarga del `.rsc`)

Criterio: modificar un router de laboratorio y visualizar el cambio.
**Cumplido** con un cambio real en un MikroTik en producción.

## Fase 4 — Auditoría (parcial)
- eventos ✅
- Security Agent ✅ (reglas `ROS-SEC-*`)
- Audit Agent ✅ (reglas `ROS-HYG-*`)
- BGP Agent ✅ (reglas `ROS-BGP-*`)
- clasificación de riesgos ✅ (severidad + puntaje 0–100)
- Diff Agent — pendiente: hoy se audita el último respaldo, no el cambio
  entre dos versiones
- Report Agent — pendiente

Criterio: producir auditorías reproducibles sin modificar el router.
**Cumplido** para el último respaldo de cada equipo: motor determinista,
read-only y sin LLM. Ver `docs/PHASE4.md`.

## Fase 5 — Dashboard
- routers
- backups
- cambios
- auditorías
- seguridad
- BGP
- agentes
- alertas

## Fase 5.5 — Multi-tenant ✅
- roles: administrador, operador, auditor, lector ✅
- cada cliente ve únicamente los equipos de su empresa (`group_name`) ✅
- aislamiento verificado por pruebas ✅ (28 casos: inventario, respaldos,
  diffs, auditoría, respaldo masivo, ajustes y cuentas)
- gestión de cuentas en el panel ✅
- bitácora: quién dio de alta, modificó o borró un equipo, pidió un respaldo o
  cambió el Git remoto ✅ (más accesos con IP y bloqueos; `docs/ACTIVITY_LOG.md`)

Criterio: un usuario de cliente entra al panel y solo ve sus propios respaldos,
sin poder enumerar los de nadie más. **Cumplido**; ver `docs/MULTITENANT.md`.

## Fase 6 — Automatización
- jobs
- aprobación
- backup previo
- piloto
- despliegue gradual
- verificación
- rollback
- auditoría completa
