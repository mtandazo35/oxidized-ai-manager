# Contexto del proyecto

## Visión
Oxidized AI Manager será una plataforma centralizada para administrar principalmente equipos MikroTik. Oxidized seguirá siendo el motor especializado en obtener configuraciones, mientras la plataforma añadirá inventario, historial, auditoría, análisis, alertas, agentes IA y, en fases posteriores, automatización segura.

## Objetivo del MVP
Conseguir un flujo completo y verificable:

MikroTik -> Oxidized -> backup -> Git -> detectar cambio -> diff -> mostrarlo por API/dashboard -> analizarlo posteriormente con un agente.

## Entorno inicial
El despliegue objetivo es una máquina Debian 13 (trixie) x86_64 que concentra todo el stack:

- Oxidized
- Git
- PostgreSQL
- Redis
- Backend/API
- MikroTik Collector
- Agent workers, Scheduler y agentes (Audit, Security, Diff, Report) cuando lleguen sus fases

Si la carga lo exige más adelante, los workers/agentes pueden separarse a una segunda máquina.

## Backups MikroTik
Se contemplan dos tipos:
1. Exportación de texto para Git, comparación y auditoría.
2. Lo que el export deja fuera y sí se puede tener en texto: certificados (PEM,
   con `export-certificate`) y los archivos del equipo. **Decidido: la plataforma
   se queda en texto plano y no persigue el respaldo binario**, porque un binario
   solo restaura ese mismo router -trae sus MAC y quiere la misma versión de
   RouterOS- mientras que un `.rsc` se importa en un repuesto. Detalles y la
   trampa del PEM cifrado en
   [ROUTEROS_RESPALDO_COMPLETO.md](ROUTEROS_RESPALDO_COMPLETO.md).

Ni los certificados ni los archivos sustituyen al export de texto: lo complementan.

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
