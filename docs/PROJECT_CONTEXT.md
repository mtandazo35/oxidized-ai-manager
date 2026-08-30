# Contexto del proyecto

## Visión
Oxidized AI Manager será una plataforma centralizada para administrar principalmente equipos MikroTik. Oxidized seguirá siendo el motor especializado en obtener configuraciones, mientras la plataforma añadirá inventario, historial, auditoría, análisis, alertas, agentes IA y, en fases posteriores, automatización segura.

## Objetivo del MVP
Conseguir un flujo completo y verificable:

MikroTik -> Oxidized -> backup -> Git -> detectar cambio -> diff -> mostrarlo por API/dashboard -> analizarlo posteriormente con un agente.

## Entorno inicial
El laboratorio está pensado para dos Raspberry Pi 4. Para reducir complejidad, la primera fase puede ejecutarse en una sola Raspberry y posteriormente distribuir workers/agentes a la segunda.

### Raspberry Pi 1
- Oxidized
- Git
- PostgreSQL
- Redis
- Backend/API
- MikroTik Collector

### Raspberry Pi 2
- Agent workers
- Scheduler
- Audit Agent
- Security Agent
- Diff Agent
- Report Agent

## Backups MikroTik
Se contemplan dos tipos:
1. Exportación de texto para Git, comparación y auditoría.
2. Backup binario de RouterOS como mecanismo adicional de recuperación.

El backup binario no sustituye al export de texto.

## Inventario
La plataforma deberá poder registrar y consultar:
- hostname
- dirección de administración
- modelo
- serial
- versión RouterOS
- arquitectura
- uptime
- CPU
- RAM
- interfaces
- direcciones IP
- routing
- BGP/OSPF cuando aplique
- servicios
- usuarios
- firewall/NAT para auditoría

## IA
La IA debe funcionar inicialmente como analista, no como operador autónomo. Analizará configuraciones y diffs, clasificará riesgos y propondrá acciones.

## Automatización futura
Los cambios deberán ejecutarse mediante jobs auditables y por lotes. Flujo obligatorio:
backup previo -> validación -> propuesta -> aprobación -> piloto -> verificación -> despliegue gradual.

Si falla una fase, detener el despliegue y ejecutar la estrategia de recuperación definida.

## Base de conocimiento
Crear estándares internos en archivos versionados, por ejemplo:
- standards/mikrotik/bgp.md
- standards/mikrotik/firewall.md
- standards/mikrotik/nat.md
- standards/mikrotik/cgnat.md
- standards/mikrotik/security.md

Los agentes deberán considerar estos estándares además de documentación técnica y configuración observada.
