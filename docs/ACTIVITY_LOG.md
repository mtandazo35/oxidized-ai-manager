# Bitácora y control de acceso

Todo en **Ajustes del sistema** (solo administrador): qué pasó, quién lo hizo y
desde qué dirección, más los tres controles que deciden quién puede entrar.

Es un registro de seguridad, así que se reserva al administrador. A un cliente
no le corresponde ver los accesos de los demás, y la propia lista de acciones
revela la estructura interna de la plataforma.

## Qué se registra

Cada petición que **cambia algo** queda anotada con la fecha, la cuenta, la IP
de origen, la acción, el resultado y el código HTTP. Las lecturas no se anotan:
llenarían la tabla sin aportar nada.

El registro lo hace un middleware, no cada endpoint, para que **una acción
nueva no se quede sin anotar por olvido**: lo que no está en la tabla de
nombres se guarda igual con su método y ruta.

| Acción | Cuándo |
| --- | --- |
| `login.ok` / `login.fail` | acceso concedido o rechazado |
| `login.denied` | la IP no está en la lista de permitidas |
| `login.blocked` / `login.locked` | la IP o la cuenta estaban bloqueadas |
| `password.change` | alguien cambió su clave |
| `equipo.alta` / `.edicion` / `.baja` / `.carga-masiva` | inventario |
| `respaldo.manual` / `.masivo` | respaldos lanzados a mano |
| `cuenta.alta` / `.edicion` / `.baja` | gestión de cuentas |
| `ajustes.cambio`, `oxidized.recarga`, `sistema.actualizacion` | operación |
| `acceso.desbloqueo-ip` / `.desbloqueo-cuenta` | desbloqueos manuales |

**Las contraseñas nunca se guardan**, ni siquiera en el intento fallido: se
anota el usuario, la IP y el motivo, jamás lo que se tecleó. Hay una prueba
que lo fija.

La bitácora se purga sola: el ciclo de fondo borra a diario lo más viejo que
`ACTIVITY_RETENTION_DAYS` (90 por defecto). Sin eso la tabla crecería sin
límite.

## Los tres controles de acceso

Se aplican **en este orden**, antes de mirar siquiera la contraseña.

### 1. Lista de IPs permitidas

Desactivada por defecto. Al activarla, solo se puede iniciar sesión desde las
redes indicadas (`10.99.99.0/24`, `190.0.0.5`, separadas por comas o líneas).

Dos salvaguardas deliberadas:

- **Una lista vacía no bloquea a nadie.** Si negara todo, activar la casilla
  sin rellenarla dejaría el panel inaccesible y habría que arreglarlo entrando
  por SSH a la base de datos.
- **No puede dejarse fuera a sí mismo**: si activa la lista y su propia IP no
  está incluida, la API lo rechaza y le dice cuál es su dirección.

`127.0.0.1` siempre entra, para que el diagnóstico local desde el propio
servidor nunca dependa de esta lista.

### 2. Bloqueo de IP

Una IP que acumule `IP_BLOCK_THRESHOLD` fallos (20) dentro de la ventana
(15 min) queda bloqueada `IP_BLOCK_MINUTES` (30). El contador se reinicia solo
si pasa la ventana sin fallos, para que un goteo lento no acabe bloqueando a un
despistado semanas después.

**Las IPs de confianza nunca se autobloquean**: ni `127.0.0.1` ni las de la
lista de permitidas. Si no, bastaría con atacar desde la red de gestión para
dejar fuera al operador.

### 3. Bloqueo de cuenta

Tras `ACCOUNT_LOCK_THRESHOLD` fallos (8) la cuenta queda bloqueada
`ACCOUNT_LOCK_MINUTES` (15), aunque después se acierte la clave. Un acceso
correcto limpia el contador.

Los dos bloqueos viven en **PostgreSQL**, no en memoria como antes: los de
memoria se borraban en cada reinicio del backend, que es justo lo que provoca
un ataque sostenido.

Sigue existiendo, además, el límite de 5 intentos por minuto y por IP del
middleware. Ese sí es en memoria porque frena el **ritmo** de peticiones, no
cuenta fallos de autenticación.

## Desbloquear

En la tarjeta «Seguridad de acceso» aparecen los bloqueos activos con un botón
para levantarlos. Por consola:

```sql
DELETE FROM ip_blocks WHERE ip = '203.0.113.9';
DELETE FROM account_locks WHERE username = 'admin';
```

## Si se queda fuera por la lista de IPs

La API no deja activarla sin incluirse, pero si cambia de red después:

```bash
docker compose exec postgres psql -U oxidized_ai -d oxidized_ai \
  -c "UPDATE settings SET value='false' WHERE key='login_allowlist_enabled';"
```

## Ajustes

| Variable | Por defecto | Qué hace |
| --- | --- | --- |
| `LOGIN_FAILURE_WINDOW_MINUTES` | 15 | ventana en la que se acumulan fallos |
| `ACCOUNT_LOCK_THRESHOLD` | 8 | fallos para bloquear una cuenta |
| `ACCOUNT_LOCK_MINUTES` | 15 | cuánto dura |
| `IP_BLOCK_THRESHOLD` | 20 | fallos para bloquear una IP |
| `IP_BLOCK_MINUTES` | 30 | cuánto dura |
| `ACTIVITY_RETENTION_DAYS` | 90 | cuánto se guarda la bitácora |

## Sobre la IP que se registra

Es la que resuelve uvicorn a partir de `X-Forwarded-For`, y **solo** se fía de
esa cabecera si la petición viene de `FORWARDED_ALLOW_IPS`. Si ve la IP del
proxy en lugar de la del cliente, ese ajuste está mal: revise
`docs/PUBLIC_ACCESS.md`. Con él mal puesto, todos los accesos parecerían venir
del proxy y los bloqueos por IP dejarían de distinguir a nadie.
