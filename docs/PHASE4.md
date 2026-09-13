# Fase 4 — Auditoría de configuraciones (parcial)

## Alcance implementado

Auditoría **determinista y read-only** sobre las configuraciones que ya están
en `backups.git`. La entrada es el texto de un respaldo; la salida, hallazgos
con evidencia. No se abre sesión contra ningún equipo, no se ejecuta nada en
los routers y no hay LLM en el camino: la misma configuración produce siempre
los mismos hallazgos, que es el criterio de aceptación de la fase.

### Piezas

- `app/routeros_export.py` — parser léxico de `/export`: une líneas partidas
  con `\`, respeta comillas y expresiones `[ find ... ]`, y expone las stanzas
  por sección (`/ip service`, `/ip firewall filter`…). Incluye
  `redact_secrets()`, que enmascara claves, PSK y comunidades.
- `app/audit_rules.py` — catálogo de reglas y motor. Cada regla declara id,
  categoría, severidad, título y recomendación, y devuelve la evidencia que la
  dispara. Agrupadas por agente lógico del roadmap:
  - `security` (Security Agent) — `ROS-SEC-*`
  - `hygiene` (Audit Agent) — `ROS-HYG-*`
  - `bgp` (BGP Agent) — `ROS-BGP-*`
- `app/audit.py` — API autenticada.

### Endpoints

| Endpoint | Qué devuelve |
| --- | --- |
| `GET /api/audit/rules` | Catálogo completo de reglas evaluadas. |
| `GET /api/audit/node?node=&commit=` | Informe de un equipo (`commit` por defecto `HEAD`). |
| `GET /api/audit/summary` | Una fila por equipo del inventario, ordenada por riesgo. |

`summary` audita el último respaldo de cada equipo con concurrencia limitada a
8 `git show` simultáneos. Un equipo sin respaldo todavía sale con
`level: "unknown"` y `error: "sin respaldo todavía"`, no como aprobado.

### Clasificación de riesgos

Severidades `critical`, `high`, `medium`, `low` con pesos 40/15/5/1. El
`score` es la suma acotada a 100 y el `level` es la severidad del hallazgo más
grave (`ok` si no hay ninguno). El puntaje sirve para **ordenar** la flota, no
como nota absoluta.

### Reglas

Seguridad: servicios en texto plano habilitados (telnet, FTP, HTTP, API),
servicios de gestión sin lista de origen, cadena `input` sin descarte,
resolutor DNS abierto a Internet, SNMP con comunidad `public`, proxy/SOCKS,
UPnP, usuario `admin` activo, usuario `full` sin `address=`, bandwidth-server,
MAC-Telnet/MAC-Winbox en todas las interfaces, SSH sin `strong-crypto`,
descubrimiento de vecinos en todas las interfaces.

Higiene: identidad de fábrica, sin cliente NTP, RouterOS por debajo de la
versión mínima soportada (`MIN_SUPPORTED_VERSION`).

BGP: sesiones sin filtro de entrada y sin filtro de salida (sintaxis de
RouterOS 7 `input.filter` / `output.filter-chain` y la heredada
`in-filter` / `out-filter`).

### Sobre los valores por defecto de `/export`

`/export` **solo imprime lo que difiere del valor de fábrica**. Telnet, FTP,
HTTP y la API vienen habilitados de fábrica, así que la *ausencia* de
`/ip service` no es un router limpio: es un router con todo abierto. Las
reglas modelan ese valor por defecto y lo dicen en la evidencia
("queda con el valor de fábrica"), para que el hallazgo sea verificable y no
parezca inventado.

## Panel

Pestaña **Auditoría**: tabla por equipo con empresa, versión de RouterOS,
insignia de riesgo, barra de puntaje y recuento por severidad; buscador; botón
«Ver reglas» con el catálogo; y un modal de hallazgos por equipo con la
evidencia (líneas del export, censuradas) y la recomendación de cada regla.

## Seguridad

Los respaldos incluyen las claves de los equipos a propósito —deben ser
restaurables—, así que **toda** la evidencia que sale por la API pasa por
`redact_secrets()`. Ver `docs/SECURITY.md`.

## Criterio de aceptación

1. Las pruebas del backend pasan (`pytest -q`).
2. Una configuración endurecida no produce hallazgos; una descuidada los
   produce con su evidencia y ordenados por severidad.
3. Dos ejecuciones sobre el mismo respaldo devuelven exactamente el mismo
   informe (reproducibilidad).
4. Ninguna respuesta de `/api/audit/*` contiene una clave de router.

## Pendiente de la fase

- Persistir los informes para ver la evolución del riesgo en el tiempo y
  alertar cuando un cambio **introduce** un hallazgo (Diff Agent sobre dos
  commits, no solo sobre el último).
- Report Agent: resumen exportable por empresa.
- Reglas con umbrales configurables desde el panel (hoy
  `MIN_SUPPORTED_VERSION` es una constante).
- Excepciones por equipo ("esto es aceptable aquí") para que la flota pueda
  llegar a cero hallazgos reales.
