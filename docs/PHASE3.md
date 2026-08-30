# Fase 3 — Respaldos a Git y eventos (parcial)

## Alcance implementado

- Oxidized guarda cada respaldo en un repositorio Git dentro del volumen
  persistente (`output: git`, repo `backups.git`), con historial completo
  por equipo. La recolección periódica queda en 3600 s (`interval`).
- Un hook `exec` de Oxidized notifica al backend cada respaldo exitoso o
  fallido: `POST /api/oxidized/events` con el header `X-Oxidized-Token`
  (mismo token del source; `oxidized-init` lo inyecta en ambos lugares).
- Tabla `backup_events` y endpoints autenticados con login:
  - `GET /api/backups/status` — último evento, último éxito y último commit
    por equipo.
  - `GET /api/backups/events?node=&limit=` — historial de eventos.
- Los eventos del nodo `phase1-placeholder` se descartan: con el inventario
  vacío ese marcador falla su recolección horaria por diseño y no debe
  ensuciar el estado.

## Programación y envío a Git remoto

- El intervalo de respaldo se configura desde el panel (sección Configuración,
  5–10080 minutos; tabla `settings`). Quien programa es el **backend**: un
  bucle en segundo plano encola cada equipo vía `GET /node/next/<name>` de
  oxidized-web, por lo que los cambios de intervalo aplican sin reiniciar
  nada. El `interval` interno de Oxidized queda anulado (1 año).
- `POST /api/devices/{id}/backup` («Respaldar ahora» en el panel) encola un
  respaldo inmediato de un equipo.
- Envío opcional a un Git remoto: toggle + URL (puede incluir token embebido,
  p. ej. `https://usuario:token@github.com/u/respaldos.git`). Tras cada ciclo
  (con 2 min de gracia para que Oxidized termine de commitear) el backend hace
  `git push` del repo `backups.git` montado de solo lectura. El resultado del
  último push se muestra en el panel; la URL nunca se devuelve con el token
  (se enmascara como `usuario:***@`).

Queda pendiente de la Fase 3: API de diffs entre versiones (leer `backups.git`
ya montado en el backend) y el backup binario de RouterOS (collector).

## Criterio de aceptación

1. Las pruebas del backend pasan (`pytest -q`).
2. Con un router real registrado, tras un ciclo de recolección existe un
   commit en `backups.git` y `GET /api/backups/status` muestra
   `last_event = node_success` con su commit.
3. Un fallo de credenciales o alcance genera `node_fail` visible en
   `/api/backups/events`.
