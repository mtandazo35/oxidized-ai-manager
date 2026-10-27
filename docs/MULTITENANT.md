# Multi-tenant y roles

Cada cuenta pertenece a una **empresa** (`group_name`, la misma con la que se
agrupan los equipos) y tiene un **rol**. Así se le puede dar acceso a un
cliente para que revise sus propios respaldos sin ver los de nadie más.

## Roles

| Rol | Ve | Puede |
| --- | --- | --- |
| `admin` | todas las empresas | todo: cuentas, ajustes globales, recargar Oxidized |
| `operador` | su empresa | alta, edición, borrado y disparo de respaldos |
| `auditor` | su empresa | solo lectura + auditoría |
| `lector` | su empresa | solo lectura |

El administrador es el único con `group_name` vacío, que significa "todas las
empresas". Una cuenta no administradora **necesita** empresa: la API rechaza
crearla sin ella (422), porque una cuenta sin empresa no vería ningún equipo y
el cliente recibiría un panel vacío sin explicación.

## Dónde se aplica el filtro

En la API, endpoint por endpoint — **no** en el panel. El panel esconde lo que
la cuenta no puede hacer, pero eso es comodidad, no control de acceso: quien
llame a la API directamente obtiene el mismo resultado.

| Endpoint | Alcance |
| --- | --- |
| `GET /api/devices` | solo los equipos de su empresa |
| `GET/PATCH/DELETE /api/devices/{id}` | 404 si el equipo es de otra empresa |
| `POST /api/devices`, `/import` | la empresa se fuerza a la propia; otra empresa → 403 |
| `POST /api/devices/{id}/backup` | 404 fuera de su empresa |
| `GET /api/backups/status`, `/events`, `/oxidized-status` | filtrados por empresa |
| `GET /api/backups/versions`, `/diff`, `/config` | 404 fuera de su empresa |
| `POST /api/backups/run` | el alcance nunca sale de su empresa, ni pasando ids ajenos |
| `GET /api/audit/summary`, `/node` | solo su empresa; requiere rol con auditoría |
| `/api/settings`, `/api/users`, `/api/oxidized/reload` | solo `admin` |

`GET /api/oxidized/nodes` es la excepción: lo consume Oxidized con su token de
servicio y necesita la flota completa para poder respaldarla. No lo usa ninguna
cuenta de usuario.

### Por qué 404 y no 403

Pedir un equipo de otra empresa responde **404**, no 403. Un 403 confirmaría
que ese equipo existe, y con eso un cliente podría enumerar los nombres de los
equipos de otro cliente probando nombres.

### Falla cerrada

El alcance se calcula en `CurrentUser.scope`: `None` para el administrador
(todas), y `group_name` para el resto. Una cuenta no administradora sin
empresa recibe `""`, que no coincide con ningún equipo agrupado: **ve nada, no
todo**. Es la dirección correcta para equivocarse y hay una prueba que lo fija.

El rol y la empresa se leen de la base en **cada** petición, no del token: un
cambio de rol aplica de inmediato y no espera a que caduque el JWT (8 h).

## Nombres de equipo reutilizados

El historial de respaldos se indexa por **nombre** de equipo: el archivo en
`backups.git`, las filas de `backup_events` y el veredicto de
`backup_integrity`. Si un nombre pasa a otro equipo —se borra y otra empresa da
de alta uno igual, alguien renombra el suyo a un nombre que ya se usó, o el
administrador mueve el equipo de empresa—, lo anterior es del dueño previo y
contiene sus claves.

- Cada equipo tiene `history_since`: se fija al darlo de alta y se renueva al
  cambiarle el nombre o la empresa. Versiones, diff, configuración, eventos,
  estado, integridad y auditoría ignoran lo anterior a esa fecha. Pedir un
  commit viejo por su hash responde **404**, igual que uno inexistente.
- El primer diff tras el corte se muestra como archivo nuevo, sin las líneas
  que quitó respecto a la versión del dueño anterior.
- Al borrar un equipo se borran también sus filas de `backup_events` y
  `backup_integrity`. El historial git no se puede purgar (el repositorio es de
  Oxidized y está montado de solo lectura): por eso existe `history_since`.
- Los equipos que ya existían antes de la columna quedan con `NULL` y conservan
  todo su historial.

Consecuencia aceptada: dar de alta de nuevo un equipo borrado por error, o
moverlo de empresa, oculta también su propio historial previo, incluso al
administrador desde el panel. Los commits siguen en `backups.git`.

### Riesgo residual: el 409 por nombre repetido

El nombre es único en todo el sistema (es el nombre del archivo en git), así
que dar de alta o renombrar con un nombre que ya usa otra empresa responde
**409**. A un no administrador se le contesta lo mismo tanto si el nombre es
suyo como si es ajeno («Ese nombre no está disponible; elija otro.»), sin
mencionar empresas. Aun así, el 409 revela que el nombre está ocupado en algún
sitio: se acepta, porque los nombres no llevan secretos y evitarlo exigiría
nombres de archivo por empresa en el repositorio de Oxidized. El administrador
recibe el mensaje preciso («Ya existe un equipo con ese nombre.»).

## Gestión de cuentas

Pestaña **Usuarios** (solo administrador) o la API:

```bash
curl -X POST http://127.0.0.1:8000/api/users \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"username":"cliente-acme","password":"...","role":"lector","group_name":"ACME"}'
```

La cuenta nace con `must_change_password`, así que el cliente define su clave
en el primer ingreso. Desde el panel se puede cambiar el rol en línea, asignar
una clave nueva y eliminar cuentas.

Dos salvaguardas: no se puede borrar la propia cuenta, ni degradar o eliminar
al **último** administrador (quedaría un sistema sin nadie que pueda crear
cuentas ni tocar los ajustes).

## Migración

El `ALTER TABLE` añade `role` con DEFAULT `'admin'` y acto seguido cambia el
default a `'lector'`. Es deliberado: las cuentas que ya existían eran
administradores y deben seguir siéndolo, mientras que cualquier alta futura sin
rol explícito cae en el rol inofensivo.

## Lo que todavía no hay

La bitácora de quién hizo qué ya existe: ver `docs/ACTIVITY_LOG.md`.

- Un cliente ve los **nombres de empresa** de sus propios equipos, nunca los de
  otras; pero el campo sigue siendo texto libre, sin una tabla de empresas.
- Autoservicio: el cliente no puede crear sus propias subcuentas.
