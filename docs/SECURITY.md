# Guía de seguridad

Esta guía describe el modelo de seguridad de Oxidized AI Manager: lo que la
plataforma ya protege, lo que el operador debe configurar y el endurecimiento
recomendado del servidor. Lea también `docs/PUBLIC_ACCESS.md`.

## Principios

- Primera etapa estrictamente **READ-ONLY**: la plataforma lee configuraciones,
  nunca escribe en los routers.
- Los agentes IA (fases posteriores) **no ejecutan comandos** en los equipos;
  toda propuesta de cambio pasará por jobs auditables con aprobación humana.
- Mínimo privilegio en cada cuenta de servicio.
- No guardar secretos reales en Git.

## Controles implementados

### Autenticación y sesión
- Login propio con usuario en PostgreSQL y clave **bcrypt** (nunca en claro).
- Tokens **JWT HS256** firmados con `APP_SECRET_KEY`, expiración 8 h.
- **Bloqueo por cuenta**: 8 intentos fallidos en 5 min bloquean el usuario
  temporalmente (complementa el rate-limit por IP de Nginx: 5/min en `/login`).
- Todos los endpoints de datos exigen token; solo `/`, la página de login y los
  health checks son públicos.

### Secretos y datos sensibles
- Las **contraseñas de los routers se cifran en reposo** (Fernet, llave derivada
  de `APP_SECRET_KEY`). Solo se descifran para entregárselas a Oxidized por la
  red interna. Un volcado de la base no expone credenciales en claro.
- Las contraseñas de routers **nunca** se devuelven por la API ni aparecen en el
  panel.
- `/api/oxidized/nodes` y `/api/oxidized/events` exigen el header
  `X-Oxidized-Token` (comparación en tiempo constante).
- Oxidized aplica `remove_secret: true` sobre las configuraciones respaldadas.
- `.env` con permisos `600`; ignorado por Git.

### Superficie de la aplicación
- `/docs` y `/openapi.json` deshabilitados salvo `APP_ENV=development`.
- Parámetros de las consultas a Git (nombre de nodo, hash de commit) validados
  con patrón estricto; no se construye ninguna shell.
- Límite de tamaño de cuerpo (8 MB) y de filas en importaciones.

### Cabeceras y transporte (Nginx público)
- TLS 1.2/1.3, redirección HTTP→HTTPS, HSTS con `includeSubDomains`.
- `Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options`,
  `Referrer-Policy: no-referrer`, `Permissions-Policy` restrictiva.
- Solo el panel/API se publica; PostgreSQL, Redis y el puerto directo de
  Oxidized nunca se exponen.

### Contenedores
- Todos con `no-new-privileges`; el backend además con `read_only` y
  `cap_drop: ALL`. PostgreSQL y Redis exigen credenciales y no publican puertos.

## Responsabilidades del operador

1. **Secretos fuertes y únicos** en `.env` (`openssl rand -hex 32`). Nunca
   reutilizar `APP_SECRET_KEY`: de ella dependen los tokens y el cifrado de
   credenciales. Cambiarla invalida sesiones y obliga a recifrar/recargar claves.
2. **Cambiar la clave de `admin`** en el primer ingreso (menú de usuario).
3. **Cuentas RouterOS de solo lectura** para los respaldos (grupo con `read,ssh`
   únicamente). No usar cuentas con permisos de escritura.
4. **Respaldo antes de cambios** en configuración de producción (`tar.gz` +
   `pg_dump`).
5. **Mantener el host actualizado** (unattended-upgrades) y las imágenes al día.
6. Para el envío a Git remoto, usar un **token de alcance mínimo** (solo ese
   repositorio); la URL con token nunca se muestra completa en el panel.

## Endurecimiento del servidor (recomendado, no automático)

En un despliegue real, además de lo anterior:

- **SSH**: desactivar `PasswordAuthentication` (solo llave), `PermitRootLogin
  prohibit-password` o un usuario dedicado con sudo. Verificar acceso por llave
  antes de cerrar la sesión.
- **Firewall (UFW)**: permitir solo 22/80/443; recordar que Docker publica
  puertos saltándose UFW — filtrar en `DOCKER-USER` si algún servicio se expone.
- **fail2ban** para SSH (y opcionalmente para el 401/429 de Nginx).
- Certificados: renovación automatizada (`oxidized-cert-renew.timer`).

## Respuesta a incidentes

- **Clave de panel comprometida**: cámbiela por API; si se perdió el acceso,
  `DELETE FROM users;` + reiniciar el backend re-siembra desde `.env`.
- **`APP_SECRET_KEY` comprometida**: rótela, reinicie, y vuelva a guardar las
  claves de los routers (el cifrado depende de ella).
- **Token de Git filtrado**: revóquelo en el proveedor y configure uno nuevo.

## Qué NO hacer

- No commitear credenciales, backups, exports de equipos, tokens, comunidades
  SNMP ni llaves privadas.
- No exponer PostgreSQL, Redis, el puerto 8888 de Oxidized ni el 8000 del
  backend directamente a Internet.
- No dar a la IA una ruta de ejecución directa sobre los routers.
