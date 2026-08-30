# Seguridad

## Reglas obligatorias
- No guardar secretos reales en Git.
- Usar variables de entorno o un gestor de secretos.
- Usar cuentas de servicio con mínimo privilegio.
- Preferir API-SSL/SSH protegidos y limitar orígenes permitidos.
- Primera etapa estrictamente READ-ONLY.
- Los agentes IA no ejecutan comandos directamente.
- Registrar acciones administrativas y jobs.
- Sanitizar configuraciones antes de enviarlas a proveedores externos de IA cuando puedan contener secretos.
- Definir timeouts y límites de concurrencia.
- Evitar cambios simultáneos masivos.
- Hacer backup antes de cambios.
- Implementar aprobación humana antes de escritura.
- Mantener un mecanismo de parada de emergencia para automatizaciones.

## Datos sensibles en configuraciones
Antes del análisis IA se deberá contemplar redacción/mascarado de:
- passwords
- secrets PPP
- SNMP communities
- claves API
- claves privadas
- tokens
- información de autenticación
