# QA del proyecto — 2026-10-27

Revisión de todo el repositorio (infraestructura, backend, panel y pruebas) y
estado de cada hallazgo. Cada corrección lleva su prueba de regresión.

**Estado: corregido.** 386 pruebas pasan (eran 267). Quedan abiertos solo los
puntos marcados como decisión de diseño o riesgo aceptado, al final.

## Crítico

| Hallazgo | Arreglo |
|---|---|
| Una instalación nueva no arrancaba: `CREATE INDEX` sobre `devices` antes de crear la tabla (`db.py`) | Índice movido tras el `ALTER`; `test_schema.py` comprueba el orden de todo el esquema |
| XSS sin autenticación: un usuario inventado con comillas se pintaba dentro de un `onclick` en «Seguridad de acceso» | `esc()` escapa `& < > " '`; cero manejadores en línea (botones con `data-accion` y un único escuchador); CSP `script-src` sin `'unsafe-inline'`, con hashes de los scripts del panel |

## Alto

| Hallazgo | Arreglo |
|---|---|
| Actualizar desde el panel desconectaba el proxy (faltaba `docker-compose.proxy.yml`) | `install.sh --proxy` escribe `COMPOSE_FILE` en `.env`; `apply-update.sh` lo añade en instalaciones viejas |
| Symlinks en el canal de actualización (root escribía donde escribe el contenedor) | Lectura con `O_NOFOLLOW`, máximo 4 KiB, solo archivos regulares; escritura atómica con `O_EXCL` y `fchown` sobre el descriptor |
| Reutilizar un nombre de equipo mostraba el historial de otra empresa | Columna `history_since`: versiones, diff, config, eventos, integridad y auditoría ignoran lo anterior; al borrar se purgan eventos e integridad |
| Más XSS por comillas (`group_name`, `board`…) | Cubierto por el nuevo `esc()` y la delegación de eventos |

## Medio

| Hallazgo | Arreglo |
|---|---|
| Sin vuelta atrás si la actualización fallaba | `git reset --keep` al commit anterior, reconstrucción y aviso en el panel |
| Rutas de systemd inconsistentes (`/opt` y `/root`) | Todas en `/opt/oxidized-ai-manager`; `install.sh --systemd` instala las unidades con la ruta real |
| Sesiones vivas tras cambiar o restablecer la clave | `token_version` en el JWT; sube al cambiar clave, rol o al cerrar sesión; `POST /api/auth/logout` |
| `must_change_password` solo en la interfaz | La API responde 403 a todo salvo `me`, `change-password`, `refresh` y `logout` |
| Se aceptaba la `APP_SECRET_KEY` de ejemplo | Rechaza `CHANGE_ME…` y claves de menos de 32 caracteres |
| Token de GitHub `https://TOKEN@…` sin enmascarar; `http://` aceptado | Enmascarado; `http://` rechazado |
| Bloqueo sostenido de la cuenta `admin` | El bloqueo por cuenta no aplica desde localhost ni desde la lista de IPs permitidas |
| La lista de IPs permitidas solo filtraba el login | Se exige en cada petición autenticada |
| El 409 revelaba equipos de otras empresas | Mismo mensaje para nombre propio o ajeno («Ese nombre no está disponible») |
| Redacción de secretos incompleta | `preshared-key`, `tcp-md5-key`, `auth-key`, WPA, comunidades SNMP y comillas escapadas |
| Falsos negativos de firewall y falso positivo BGP | `drop` con protocolo o deshabilitado ya no «cierra» la cadena; ~45 condiciones restrictivas; `remote.address` con prefijo |
| Panel: `logout()` sin limpiar, errores de login mal traducidos, carga masiva fila a fila, `refresh()` mudo, ventanas sin scroll, Escape cerraba la ventana equivocada | Todo corregido (ver `test_panel_js.py`) |
| Panel: no se podía cambiar la empresa de una cuenta; degradar a un admin daba 422 | Botón «cambiar» y petición de empresa al quitar el rol de admin; confirmación al cambiar rol |
| `cryptography` limitado a `<46` | `>=46.0.5,<51.0` (probado con 50.0.2) |
| Sin rotación de logs, sin CI | `x-logging` en compose; `.github/workflows/tests.yml` (pytest, shellcheck, compose config, CSS al día) |

## Bajo (corregidos)

- Enumeración de usuarios por tiempo: bcrypt contra un hash de relleno.
- Login con usuario de más de 128 caracteres: 401 sin registrarlo.
- Orden de middlewares: cabeceras de seguridad en todas las respuestas (también 429 y 500); el 429 del limitador queda en la bitácora.
- Límite de 8 MB en la propia aplicación (413).
- Rotar `APP_SECRET_KEY` ya no corrompe las claves en silencio: error explícito y 503.
- `compare_digest` con cabecera no ASCII: 401 en vez de 500.
- `/health/backups` responde 503 si falla la base.
- `process.wait()` tras `kill()` en git, scheduler y actualizador.
- Errores de git sin rutas internas en las respuestas.
- Carrera en `update_device`: el filtro de empresa va en el propio `UPDATE`.
- Mensajes del backend en español.
- Panel: descargas `.txt` para equipos que no son RouterOS, respuestas fuera de orden descartadas, tope de 20 min al esperar una actualización, `aria-live` en los avisos, roles de diálogo, patrón del campo usuario inválido en Chrome.
- Respaldo del sistema: suma `sha256`, sin paquetes en claro si falla el cifrado.
- Opcional: `UPDATE_REQUIRE_SIGNED=1` exige commits firmados antes de actualizar.

## Decisiones de diseño y riesgos aceptados

- **El rol `lector` descarga configuraciones completas con secretos.** Es lo que
  dice la tabla de permisos; si se quiere restringir, es un cambio de producto.
- **Los nombres de equipo son únicos en toda la plataforma** (son archivos en
  Git): una empresa puede saber que un nombre está ocupado, no de quién.
- **Volver a dar de alta un equipo, o moverlo de empresa, oculta su historial
  anterior** también al administrador (consecuencia de `history_since`).
- **Oxidized-web no tiene autenticación**; solo escucha en `127.0.0.1` del host.
- **Redis** sigue en el stack aunque hoy solo lo usa el healthcheck.
- Con `FORWARDED_ALLOW_IPS` mal configurado, el límite de intentos afecta a
  todos a la vez; `install.sh --proxy` lo configura bien.
