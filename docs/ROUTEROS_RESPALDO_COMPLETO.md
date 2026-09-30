# Por qué un respaldo de RouterOS puede salir incompleto

Un respaldo que falla se ve: el equipo queda en rojo y alguien pregunta. Un
respaldo que **se guarda incompleto y en verde** no se ve, y es el que de verdad
duele, porque nadie lo descubre hasta que hay que restaurar.

RouterOS tiene dos formas de entregar un respaldo incompleto sin que nada dé
error. Las dos se dieron en esta plataforma y las dos están corregidas; este
documento explica qué pasaba, cómo se arregló y cómo comprobarlo, porque son
detalles que no se deducen leyendo el código.

## 1. El export llega cortado a la mitad

### Qué pasaba

El servidor SSH de MikroTik recorta la salida al tamaño de **ventana de canal**
que anuncia el cliente. En net-ssh —la librería que usa Oxidized— ese valor por
defecto es `0x20000`, o sea **131.072 bytes, 128 KiB**. OpenSSH anuncia 2 MB, y
de ahí el síntoma más desconcertante: un `ssh router "/export" > archivo` a mano
sale completo, y Oxidized, desde el mismo servidor y con la misma cuenta, corta.

Hacen falta dos condiciones **a la vez**, y por eso no afectaba a toda la flota:

1. que el export supere los ~128 KiB, y
2. que haya latencia suficiente para que la ventana TCP crezca por encima de la
   ventana del canal.

En LAN no se dispara nunca, aunque el config sea enorme. Contra un equipo remoto
con un config grande —un core, típicamente— sí.

Lo peor es cómo falla: MikroTik cierra el canal **limpiamente**. El `exec!` de
net-ssh devuelve lo que alcanzó a leer, sin lanzar ninguna excepción, y Oxidized
commitea ese archivo a medias como un respaldo correcto. Nunca hubo una alerta
porque para Oxidized nunca hubo un error.

No confundirlo con `timelimit`: cuando ese salta, Oxidized marca el nodo como
fallido y **no guarda nada**, así que queda el respaldo anterior. Un nodo en
verde con el archivo corto no es un problema de tiempo.

### Cómo se arregló

Subiendo la ventana de canal a 2 MB. Upstream ya lo hizo
([issue #3867](https://github.com/ytti/oxidized/issues/3867)), pero el cambio
está en `[Unreleased]`: la última versión publicada es la 0.37.0 del 2026-05-20,
que es la que usamos, y no lo trae. Tampoco se puede resolver por configuración,
porque Oxidized fija `max_win_size` en código sin exponerlo como variable.

Así que el servicio `oxidized` construye una imagen propia sobre la oficial y le
aplica ese cambio en tiempo de build: [`oxidized/Dockerfile`](../oxidized/Dockerfile).
El build falla si el parche no aplica o deja Ruby inválido, y no hace nada si la
imagen base ya lo trae —es decir, el día que salga la versión con el arreglo, el
parche se desactiva solo y el Dockerfile se puede borrar.

### Cómo comprobarlo

El tamaño es la firma del bug. Un respaldo cortado por esto pesa unos 131.000
bytes, siempre:

```sh
docker compose exec oxidized sh -c 'cd /home/oxidized/.config/oxidized/backups.git \
  && git ls-tree -r --long HEAD | sort -k4 -n'
```

Si un equipo aparece rondando esa cifra mientras el resto varía libremente, es
esto. Para verlo más de cerca, que un archivo termine a media línea es
concluyente:

```sh
docker compose exec oxidized sh -c 'cd /home/oxidized/.config/oxidized/backups.git \
  && git show HEAD:NODO | tail -3'
```

## 2. El export llega completo pero sin los secretos

### Qué pasaba

Desde **RouterOS 6.43**, `/export` oculta los datos sensibles por defecto y hay
que pedir `show-sensitive` para que salgan. El modelo `routeros` de Oxidized solo
lo pide cuando detecta versión 7 o superior; para todo lo demás manda `/export` a
secas.

Resultado: en toda la flota v6 los respaldos salían sin contraseñas PPPoE, sin
PSK de wireless, sin claves IPsec ni WireGuard y sin comunidades SNMP. El archivo
existía, tenía buen tamaño, el panel lo daba por bueno y la auditoría lo leía sin
problemas — y no servía para restaurar nada.

### Cómo se arregló

Con un modelo propio, [`oxidized/model/routeros.rb`](../oxidized/model/routeros.rb),
que invierte el criterio: pide `show-sensitive` **salvo** que detecte
positivamente una versión anterior a 6.43. Si no puede leer la versión, también
lo pide, porque un export censurado es un daño silencioso y un argumento no
soportado en un RouterOS antiquísimo es un fallo ruidoso, que se ve y se corrige.

De paso arregla otra cosa del modelo original: leía la versión con
`/([0-9])/.match(version_line)[0]` sobre un valor que podía ser `nil`, así que un
equipo que no respondiera como se espera a `/system package update print` se
quedaba **sin ningún respaldo** por un `NoMethodError`.

> **Ojo al mantenerlo.** Un modelo en `~/.config/oxidized/model/` *reemplaza* al
> que trae Oxidized, no lo extiende. Al subir la versión de Oxidized hay que
> comparar ese archivo contra el `routeros.rb` nuevo de upstream y traer lo que
> haya cambiado.

### El requisito en el router

`show-sensitive` no basta por sí solo: la cuenta de respaldo necesita la policy
`sensitive` además de `ssh,read`.

```
/user group add name=respaldo policy=ssh,read,sensitive
/user add name=respaldo group=respaldo address=<IP del servidor>/32 password=...
```

Son dos condiciones independientes y las dos son obligatorias. Durante un tiempo
la documentación de este repo afirmaba que con la policy era suficiente, que es
lo que hizo que el problema pasara desapercibido.

## La red de seguridad: verificación de integridad

Los dos fallos anteriores comparten la propiedad que los hace peligrosos: el
respaldo se guarda y el estado queda en verde. Arreglar las causas conocidas no
protege de la siguiente, así que el backend mide cada respaldo que entra y lo
compara con la versión anterior del mismo equipo:
[`backend/app/integrity.py`](../backend/app/integrity.py).

Avisa cuando el respaldo llega vacío, cuando pesa justo lo que mide la ventana
SSH, cuando encoge de forma desproporcionada, cuando desaparecen secciones que
antes estaban, cuando no trae la cabecera con la versión y cuando los secretos
salen censurados.

No bloquea ni descarta nada: un respaldo cortado también es información, y
descartarlo dejaría al equipo sin nada. Lo que hace es dejar de mentir sobre su
estado. En la pestaña **Estado de respaldos** el equipo aparece con un aviso ⚠
junto al nombre, y al pulsarlo salen los motivos.

## Lo que sigue faltando: el respaldo binario

Aunque el `/export` salga completo y con secretos, [la documentación de
MikroTik](https://help.mikrotik.com/docs/spaces/ROS/pages/328155/Configuration+Management)
dice que **nunca** incluye:

- las contraseñas de los usuarios del sistema ni sus claves SSH — *«can not be
  exported»*
- los certificados instalados (la parte privada)
- las claves de host SSH
- los archivos del equipo: páginas de hotspot, scripts guardados como archivo,
  imágenes de contenedores
- las bases de Dude y User Manager

Eso solo se conserva con el respaldo **binario** de RouterOS, que sigue pendiente
(ver [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) y [PHASE3.md](PHASE3.md)). El
binario no sustituye al export de texto: el texto es el que se puede versionar,
comparar y auditar. Son complementarios.
