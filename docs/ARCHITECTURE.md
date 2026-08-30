# Arquitectura

## Componentes

### Frontend
Next.js + Tailwind. Se añadirá después del núcleo funcional.

### Backend
FastAPI será la API central y administrará inventario, backups, eventos, auditorías, jobs y usuarios.

### PostgreSQL
Fuente principal para datos estructurados y auditoría.

### Redis
Cola/estado temporal para trabajos y agentes.

### Oxidized
Motor de recolección de configuraciones. Mantenerlo desacoplado para facilitar actualizaciones upstream.

### Git
Versionado de configuraciones de texto y fuente de historial/diffs.

### MikroTik Collector
Servicio especializado en RouterOS mediante API/API-SSL, SSH y opcionalmente SNMP.

### Agent Worker
Ejecutará agentes especializados. Los LLM no deben disponer de una ruta directa de ejecución sobre routers.

## Flujo de respaldo
Router -> Oxidized -> configuración -> Git -> evento -> Diff Agent -> clasificación -> API/UI.

## Flujo futuro de cambio
Usuario -> propuesta -> validación -> backup -> aprobación -> piloto -> verificación -> lotes -> auditoría.

## Distribución inicial
Todo el stack corre en una máquina Debian 13 x86_64. Separar workers a una segunda máquina solo si la carga lo exige tras el MVP.

## Escalabilidad
No introducir Kubernetes, Kafka ni infraestructura distribuida compleja durante el MVP. Docker Compose es suficiente.
