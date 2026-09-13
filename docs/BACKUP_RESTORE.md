# Respaldo y restauración de la plataforma

Oxidized respalda los routers. Esto respalda **la plataforma**: sin estas tres
piezas no se puede reconstruir el servicio.

| Pieza | Qué contiene | Si se pierde |
| --- | --- | --- |
| PostgreSQL | inventario, usuarios, eventos, ajustes | hay que recargar todo el inventario a mano |
| Volumen `oxidized_data` | `backups.git` con **todo** el historial de configuraciones | se pierde el histórico completo |
| `.env` | `APP_SECRET_KEY` | las claves de routers del volcado quedan **indescifrables** |

El `.env` es el punto que se suele olvidar: las contraseñas de los routers se
guardan cifradas con una llave derivada de `APP_SECRET_KEY`, así que un volcado
de PostgreSQL sin su `.env` es inservible.

## Respaldo automático diario

```bash
sudo cp deploy/oxidized-ai-manager-backup.service /etc/systemd/system/
sudo cp deploy/oxidized-ai-manager-backup.timer   /etc/systemd/system/

# Frase de cifrado, fuera del unit y con permisos estrictos
printf 'BACKUP_PASSPHRASE=%s\n' "$(openssl rand -hex 24)" \
    | sudo tee /etc/oxidized-ai-manager-backup.env >/dev/null
sudo chmod 600 /etc/oxidized-ai-manager-backup.env

sudo systemctl daemon-reload
sudo systemctl enable --now oxidized-ai-manager-backup.timer
sudo systemctl start oxidized-ai-manager-backup.service   # primera corrida
```

Guarde esa frase en su gestor de contraseñas: **sin ella el respaldo cifrado no
se puede abrir**. Los paquetes quedan en `/root/backups/` con retención de 14
días (`RETENTION_DAYS`).

A mano:

```bash
BACKUP_PASSPHRASE='...' ./scripts/backup-system.sh /root/backups
```

## Sacar el paquete del servidor

Un respaldo que solo vive en el servidor respaldado no es un respaldo. Cópielo
a otra máquina; va cifrado con AES-256, así que puede viajar por un canal que
no controle del todo:

```bash
scp /root/backups/oxidized-ai-manager-*.tar.gz.enc destino:/ruta/segura/
```

## Restauración

En un Debian limpio, con Docker instalado:

```bash
# 1. Descifrar y desempaquetar
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
    -in oxidized-ai-manager-FECHA.tar.gz.enc -out paquete.tar.gz \
    -pass env:BACKUP_PASSPHRASE
mkdir restauracion && tar xzf paquete.tar.gz -C restauracion

# 2. Repositorio y .env
git clone https://github.com/mtandazo35/oxidized-ai-manager.git /opt/oxidized-ai-manager
cd /opt/oxidized-ai-manager
cp /ruta/restauracion/env.backup .env && chmod 600 .env

# 3. Levantar solo la base para restaurarla
docker compose up -d postgres
docker compose exec -T postgres psql -U oxidized_ai -d oxidized_ai \
    < /ruta/restauracion/postgres.sql

# 4. Devolver el volumen de configuraciones
docker volume create oxidized-ai-manager_oxidized_data
docker run --rm -v oxidized-ai-manager_oxidized_data:/data \
    -v /ruta/restauracion:/in alpine:3.22 \
    sh -c 'tar xzf /in/oxidized_data.tar.gz -C /data'

# 5. Levantar el resto
docker compose up -d
```

## Verificación (hágala, no la suponga)

Tras restaurar, compruebe las cuatro cosas que fallan cuando algo salió mal:

```bash
curl -fsS http://127.0.0.1:8000/health/ready            # 1. dependencias
curl -fsS http://127.0.0.1:8000/health/backups          # 2. frescura
# 3. inventario y claves: el login funciona y los equipos están
# 4. historial: un diff de un equipo debe abrirse en el panel
```

Si el panel muestra los equipos pero Oxidized falla al conectarse a todos, casi
seguro el `.env` restaurado no es el que corresponde a ese volcado:
`APP_SECRET_KEY` no coincide y las claves no se descifran.

**Pruebe la restauración completa en otra máquina cada cierto tiempo.** Un
procedimiento de restauración que nunca se ejecutó no está probado.
