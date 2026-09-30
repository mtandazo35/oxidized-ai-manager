# Por qué un respaldo de RouterOS puede salir incompleto

Un respaldo que falla se ve: el equipo queda en rojo y alguien pregunta. Un
respaldo que **se guarda incompleto y en verde** no se ve, y es el que de verdad
duele, porque nadie lo descubre hasta que hay que restaurar.

Este documento cubre tres cosas: el fallo real que dejó a un core con la mitad de
su configuración durante meses, cómo se pide el export con secretos en cada
versión de RouterOS —que no es igual en la 6 y en la 7, y equivocarse rompe el
respaldo— y la verificación que se añadió para que la próxima vez no haga falta
que alguien sospeche.

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

La ventana no es un tope duro al total de bytes: se va reponiendo. El fallo
aparece cuando el router tiene más de una ventana en vuelo, y eso depende del
RTT. Se vio clarísimo en esta plataforma: de dos cores con export mayor de
128 KiB, solo el remoto se cortaba. El otro entregaba 219 KB sin problema por
tener menos latencia.

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

Medido en producción sobre el core afectado: **135.868 → 188.537 bytes** en el
primer respaldo tras el cambio. Eran 52 KB de configuración que se perdían en
cada ciclo.

### Cómo comprobarlo

El tamaño es la firma del bug. Un respaldo cortado por esto pesa unos 131.000
bytes, siempre:

```sh
docker compose exec oxidized git -c safe.directory='*' \
  --git-dir=/home/oxidized/.config/oxidized/backups.git ls-tree -r --long HEAD
```

Si un equipo aparece rondando esa cifra mientras el resto varía libremente, es
esto. Que un archivo termine a media línea es concluyente:

```sh
docker compose exec oxidized git -c safe.directory='*' \
  --git-dir=/home/oxidized/.config/oxidized/backups.git show HEAD:NODO | tail -3
```

Ojo al contar líneas para comparar: el modelo **une las líneas partidas** con `\`
que escribe RouterOS, así que el archivo guardado tiene bastante menos líneas que
lo que se ve en la terminal del router. Para comparar, usar bytes.

## 2. Los secretos: el export no se pide igual en la 6 y en la 7

### Cómo es de verdad

- **RouterOS 7** oculta los datos sensibles por defecto. Hay que pedir
  `/export show-sensitive` para que salgan.
- **RouterOS 6** los incluye en el `/export` normal, y **no acepta**
  `show-sensitive`.

El modelo de Oxidized ya elige el comando según la versión que detecta, y hace
bien. No hay nada que arreglar aquí.

En las **dos** versiones hace falta que la cuenta de respaldo tenga la policy
`sensitive`; sin ella el export sale censurado aunque el comando sea el correcto.

### El error que cometimos, para no repetirlo

Se cambió el modelo para pedir `show-sensitive` siempre, creyendo que RouterOS
6.43 había invertido el comportamiento también en la rama 6. Un CCR1036 con
**6.49.21** contestó:

```
expected end of command (line 1 column 9)
```

…y ese texto de error se guardó como si fuera el respaldo del equipo: **219.903
bytes de configuración pasaron a 7.293 bytes de error**, y el nodo siguió en
verde. Se revirtió en minutos porque la verificación de integridad lo marcó sola.

De dónde venía la confusión, que es lo que conviene recordar:

- La página de [Configuration
  Management](https://help.mikrotik.com/docs/spaces/ROS/pages/328155/Configuration+Management)
  dice *«By default, sensitive information is hidden»*. Es cierto, pero documenta
  **RouterOS 7**.
- La entrada del changelog de 6.43 sobre export y datos sensibles habla de
  `hide-sensitive`, no de cambiar el valor por defecto.

La comprobación que zanja la duda es mirar un export v6 real: los secretos
aparecen en claro, sin comillas, en el `/export` a secas.

```sh
# En un respaldo de un equipo con RouterOS 6, esto devuelve valores:
grep -oE '[a-z-]*password=' NODO | sort | uniq -c
```

### Qué sí se cambió del modelo

Una sola cosa, en [`oxidized/model/routeros.rb`](../oxidized/model/routeros.rb):
el modelo original lee la versión con `/([0-9])/.match(version_line)[0]` sobre un
valor que puede ser `nil`, así que un equipo que no responda a `/system package
update print` como se espera se queda **sin ningún respaldo**, por un
`NoMethodError`, por no haber podido leer su número de versión. Ahora una versión
ilegible vale 0 y cae en la rama del `/export` a secas, que es un export válido en
cualquier RouterOS: lo correcto cuando no se sabe con qué se está hablando.

> **Ojo al mantenerlo.** Un modelo en `~/.config/oxidized/model/` *reemplaza* al
> que trae Oxidized, no lo extiende. Al subir la versión de Oxidized hay que
> comparar ese archivo contra el `routeros.rb` nuevo de upstream y traer lo que
> haya cambiado.

### El requisito en el router

```
/user group add name=respaldo policy=ssh,read,sensitive
/user add name=respaldo group=respaldo address=<IP del servidor>/32 password=...
```

Nunca dar `write`, `api` ni `policy` a esta cuenta, y limitar el origen con
`address=`.

## La red de seguridad: verificación de integridad

Todo lo anterior comparte la propiedad que lo hace peligroso: el respaldo se
guarda y el estado queda en verde. Arreglar las causas conocidas no protege de la
siguiente —y el episodio del `show-sensitive` demuestra que la causa siguiente
puede ser un cambio propio—, así que el backend mide cada respaldo que entra y lo
compara con la versión anterior del mismo equipo:
[`backend/app/integrity.py`](../backend/app/integrity.py).

Avisa cuando el respaldo llega vacío, cuando **no contiene ninguna sección de
configuración** (la señal que delató el error del `show-sensitive` sin necesidad
de comparar con nada), cuando pesa justo lo que mide la ventana SSH, cuando
encoge de forma desproporcionada, cuando desaparecen secciones que antes estaban,
cuando no trae la cabecera con la versión y cuando los secretos salen censurados.

El encogimiento exige las dos cosas a la vez: caer por debajo del 70% y perder al
menos 2 KB. Solo el porcentaje daba falsas alarmas en equipos con configuración
pequeña, donde quitar tres líneas ya pasa del 30%.

No bloquea ni descarta nada: un respaldo cortado también es información, y
descartarlo dejaría al equipo sin nada. Lo que hace es dejar de mentir sobre su
estado. En la pestaña **Estado de respaldos** el equipo aparece con un aviso ⚠
junto al nombre, y al pulsarlo salen los motivos.

## Lo que sigue faltando, y por qué no es una sola cosa

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

Lo tentador es dar por hecho que el respaldo **binario** cubre todo eso. No es
así, y conviene tenerlo claro antes de diseñar la fase que falta. Según la
[página de Backup](https://help.mikrotik.com/docs/display/ROS/Backup):

- **No trae Dude ni User Manager**: *«If The Dude or User-manager is installed on
  the router, then the system backup will not contain configuration from these
  services»*. Esas dos bases no están **ni** en el export **ni** en el binario;
  necesitan su propio mecanismo.
- **No viene cifrado por omisión**: *«Without a provided password, the backup file
  is unencrypted»*. Antes de la 6.43 se cifraba con la clave del usuario actual;
  desde la 6.43 el valor por defecto pasó a ser sin cifrar. Un colector que no
  pase `password=` explícitamente estaría guardando un clon completo de cada
  router en claro. Algoritmo: AES-SHA256; el RC4 que también admite está *«only
  available for compatibility reasons»*.
- **No es portable a otro equipo**: *«includes the device's MAC addresses, which
  are restored when the backup file is loaded»*, y recomiendan restaurar sobre la
  misma versión de RouterOS. Es un clon del mismo router, no un respaldo que se
  pueda llevar a un repuesto.
- **De los certificados, la documentación no dice nada** — ni que los incluya ni
  que no. Al no poder asegurarlo, no se puede depender del binario para eso.

La vía documentada para los certificados es sacarlos aparte con
`/certificate export-certificate ... export-passphrase=<frase>`: con esa frase
*«also encrypted private KEY file will be exported»*. Es el único de los tres
mecanismos que entrega la clave privada cifrada por sí mismo.

Así que la fase pendiente son **tres** recolectores, no uno, y ninguno sustituye a
los otros (ver [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) y [PHASE3.md](PHASE3.md)):

| Mecanismo | Qué aporta | Cuidado |
| --- | --- | --- |
| `/export` (ya hecho) | versionable, comparable, auditable | no trae claves de usuario, certificados ni archivos |
| `/system backup save` | clon completo del equipo | hay que pasar `password=`; ligado a ese router; sin Dude ni User Manager |
| `/certificate export-certificate` | certificados con la clave privada cifrada | requiere frase de paso y guardarla aparte |

Los dos últimos son binarios o cifrados, así que **no deben ir a `backups.git`**:
Git los guardaría como blobs opacos, sin diff posible y engordando el histórico en
cada ciclo. Necesitan almacenamiento propio con su política de retención.
