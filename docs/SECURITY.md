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
  temporalmente. Lo complementa el **rate limit por IP** de la propia
  aplicación (5/min en `/api/auth/login`): el primero frena la fuerza bruta
  contra una cuenta, el segundo a un origen que rota nombres de usuario.
- Todos los endpoints de datos exigen token; solo `/`, la página de login y los
  health checks son públicos.

### Autorización y aislamiento entre clientes
- Cada cuenta tiene un **rol** (`admin`, `operador`, `auditor`, `lector`) y una
  **empresa**. El filtrado por empresa se aplica en la API, endpoint por
  endpoint; el panel solo esconde lo que la cuenta no puede hacer.
- Pedir un recurso de otra empresa responde **404, no 403**: un 403
  confirmaría que existe y permitiría enumerar equipos ajenos.
- El alcance **falla cerrado**: una cuenta no administradora sin empresa no ve
  todo, no ve nada.
- Rol y empresa se leen de la base en cada petición, no del token: revocar o
  degradar una cuenta aplica al instante, sin esperar a que caduque el JWT.
- No se puede borrar la propia cuenta ni degradar/eliminar al último
  administrador.
- 28 pruebas fijan el aislamiento (inventario, respaldos, diffs, auditoría,
  respaldo masivo, ajustes y cuentas). Ver `docs/MULTITENANT.md`.

### Secretos y datos sensibles
- Las **contraseñas de los routers se cifran en reposo** (Fernet, llave derivada
  de `APP_SECRET_KEY`). Solo se descifran para entregárselas a Oxidized por la
  red interna. Un volcado de la base no expone credenciales en claro.
- Las contraseñas de routers **nunca** se devuelven por la API ni aparecen en el
  panel.
- `/api/oxidized/nodes` y `/api/oxidized/events` exigen el header
  `X-Oxidized-Token` (comparación en tiempo constante).
- Los respaldos son **completos y restaurables**: `remove_secret` está
  deliberadamente desactivado, así que las configuraciones guardadas en
  `backups.git` **contienen las claves de los equipos** (usuarios locales,
  PPPoE, PSK de wireless, comunidades SNMP, IPsec). Consecuencias que el
  operador debe asumir:
  - El volumen `oxidized_data` es material sensible: cífrelo o resguárdelo
    con el mismo cuidado que un gestor de contraseñas.
  - Si activa el envío a un Git remoto, **esas claves salen del servidor**: use
    un repositorio privado y un token de alcance mínimo.
  - La cuenta de respaldo del MikroTik necesita la policy `sensitive` (además
    de `read,ssh`) para que `/export` incluya los secretos.
  - La API de auditoría **censura** los valores sensibles de cada evidencia
    (`password=***`); el texto completo solo se obtiene por los endpoints de
    respaldo, que exigen token de sesión.
- `.env` con permisos `600`; ignorado por Git.

### Auditoría de configuraciones (Fase 4)
- El motor de reglas es **determinista y sin red**: recibe el texto de un
  respaldo ya almacenado y devuelve hallazgos. No abre sesión contra ningún
  equipo, no ejecuta comandos y no propone escrituras automáticas.
- `backups.git` está montado de **solo lectura** en el backend.
- Toda la evidencia pasa por `redact_secrets()` antes de salir por la API.
- `/api/audit/*` exige token de sesión, igual que el resto de los datos.

### Superficie de la aplicación
- `/docs` y `/openapi.json` deshabilitados salvo `APP_ENV=development`.
- Parámetros de las consultas a Git (nombre de nodo, hash de commit) validados
  con patrón estricto; no se construye ninguna shell.
- Límite de tamaño de cuerpo (8 MB) y de filas en importaciones.

### Cabeceras y transporte (proxy externo)
El panel se publica con un **Nginx Proxy Manager externo**, que no forma parte
de este repositorio. El reparto de responsabilidades es deliberado:

- **La aplicación** emite `Content-Security-Policy`, `X-Frame-Options: DENY`,
  `X-Content-Type-Options`, `Referrer-Policy: no-referrer`,
  `Permissions-Policy` y `Cross-Origin-Opener-Policy` en cada respuesta, y
  aplica un **rate limit de login de 5/min por IP**. Vive aquí porque es lo
  primero que se pierde cuando alguien recrea un proxy host en NPM.
- **HSTS** lo emite la aplicación solo sobre HTTPS y solo con
  `APP_ENABLE_HSTS=true`: anunciarlo antes de tener un certificado de confianza
  deja al navegador clavado en HTTPS para ese host.
- **El proxy** aporta TLS, HTTP/2, redirección a HTTPS, `client_max_body_size`
  (súbalo a 8m o fallan las cargas `.xlsx`) y la lista de acceso por IP/VPN.
- El backend confía en `X-Forwarded-For` **solo** desde `FORWARDED_ALLOW_IPS`
  (la subred Docker del proxy). Nunca `*`: permitiría falsificar la IP de
  origen —y con ella el rate limit— a cualquiera que alcance el puerto 8000.
- Solo el panel/API se publica; PostgreSQL, Redis y el puerto directo de
  Oxidized nunca se exponen, ni en el proxy ni en la LAN.
- `/health/live`, `/health/ready` y `/health/backups` son públicos para el
  monitoreo externo y devuelven solo estado y recuentos, nunca nombres de
  equipos.

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
   repositorio). La URL se guarda **cifrada** en PostgreSQL (misma llave que
   las claves de routers) y nunca se muestra completa en el panel.
7. **Respaldar la plataforma**, no solo los routers: `scripts/backup-system.sh`
   y `docs/BACKUP_RESTORE.md`. Sin el `.env` el volcado de PostgreSQL es
   indescifrable. Probar la restauración en otra máquina periódicamente.
8. **No exponer el panel a Internet** aunque tenga HTTPS: publicarlo tras la
   VPN (WireGuard/Tailscale) o con una lista de acceso por IP en el proxy.

## Endurecimiento del servidor (recomendado, no automático)

En un despliegue real, además de lo anterior:

- **SSH**: desactivar `PasswordAuthentication` (solo llave), `PermitRootLogin
  prohibit-password` o un usuario dedicado con sudo. Verificar acceso por llave
  antes de cerrar la sesión.
- **Firewall (UFW)**: permitir solo 22/80/443; recordar que Docker publica
  puertos saltándose UFW — filtrar en `DOCKER-USER` si algún servicio se expone.
- **fail2ban** para SSH (y opcionalmente para el 401/429 de Nginx).
- Certificados: los gestiona el proxy externo (Nginx Proxy Manager), no este
  stack.
- **Respaldo de la plataforma**: `oxidized-ai-manager-backup.timer` diario, con
  el paquete cifrado y copiado fuera del host (`docs/BACKUP_RESTORE.md`).

## Respuesta a incidentes

- **Clave de panel comprometida**: cámbiela por API; si se perdió el acceso,
  `DELETE FROM users;` + reiniciar el backend re-siembra desde `.env`.
- **`APP_SECRET_KEY` comprometida**: rótela, reinicie, y vuelva a guardar las
  claves de los routers **y la URL del Git remoto** (el cifrado de ambas
  depende de ella). No hay recifrado automático: es un cambio manual.
- **Token de Git filtrado**: revóquelo en el proveedor y configure uno nuevo.

## Qué NO hacer

- No commitear credenciales, backups, exports de equipos, tokens, comunidades
  SNMP ni llaves privadas.
- No exponer PostgreSQL, Redis, el puerto 8888 de Oxidized ni el 8000 del
  backend directamente a Internet.
- No dar a la IA una ruta de ejecución directa sobre los routers.
