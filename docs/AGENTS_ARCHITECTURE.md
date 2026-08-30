# Arquitectura de agentes

## Backup Agent
Supervisa trabajos de respaldo, fallos, antigüedad y solicitudes manuales.

## Diff Agent
Compara configuraciones y genera una representación estructurada de los cambios.

## Audit Agent
Evalúa configuración contra estándares internos.

## Security Agent
Busca exposición de servicios, configuraciones riesgosas y desviaciones de seguridad.

## MikroTik Agent
Interpreta información específica de RouterOS y ayuda a correlacionar inventario/configuración.

## BGP Agent
Analiza sesiones, prefijos, filtros, rutas necesarias y consistencia de políticas.

## Report Agent
Genera resúmenes de estado y hallazgos.

## Restricción
Durante las primeras fases todos los agentes son analíticos. Ningún agente tiene permiso directo para ejecutar modificaciones en routers.

## Ejecución futura
Cualquier propuesta de escritura deberá convertirse en un Job estructurado que pase por políticas, backup, aprobación, piloto, verificación y auditoría.
